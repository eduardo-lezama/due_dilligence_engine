"""
Peers Node — fetcha FinancialsReport para cada peer ticker aportado por el usuario.

Qué hace este módulo:
    Implementa peers_node, un nodo tool-lane (sin LLM) que recibe
    state["peers"] (lista de tickers que el usuario proporcionó en Checkpoint 1),
    fetcha sus datos XBRL, y actualiza CompetitiveReport.peer_roic_comparison.

Por qué peer_roic_comparison se construye AQUÍ y no en competitive_agent:
    competitive_agent corre en el fan-out ANTES de checkpoint1 y peers_node.
    Cuando competitive_agent ejecuta, state["peer_financials"] aún no existe.
    peers_node corre DESPUÉS de checkpoint1, cuando state["competitive"] ya está
    disponible — es el único punto del grafo con acceso a ambos datos a la vez.

Degraded mode:
    - Lista vacía o ausente → devuelve {} sin llamar a EDGAR.
    - Un peer que falla → se loguea y se omite; el resto continúa.
    - Nunca bloquea el pipeline completo. Los peers son contexto opcional.

Referencia ADR: ADR-015.
"""

from __future__ import annotations

import structlog

from due_diligence.agents.valuation_math import net_claims
from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import PeerROIC, PeerValuation
from due_diligence.tools.edgar import DataUnavailableError, EdgarAdapter
from due_diligence.tools.polygon import PolygonAdapter

log = structlog.get_logger()

# Mínimo de peers de la misma industria para poder descartar los de otra (con menos, una mediana
# no es una comparación y se conservan todos marcados como cross-industry).
MIN_SAME_INDUSTRY_PEERS = 2



def _opt_value(rep, field: str) -> float | None:
    """Valor de un campo Optional[MetricValue] del FinancialsReport del peer, o None.

    isinstance en lugar de `is not None`: el campo es Optional[MetricValue] por contrato, y así
    un objeto parcial (reports de versiones previas sin los campos de balance de T8) no rompe el
    cálculo — el componente ausente cuenta como 0 en net_claims, igual que en el sujeto.
    """
    v = getattr(rep, field, None)
    return v.value if isinstance(v, MetricValue) else None


def _get_sic(ticker: str) -> tuple[str | None, str | None]:
    """
    Recupera SIC code e industria de un ticker via edgartools.

    Helper para validación de calidad del peer set (EC-42). Si el subject
    es SIC 2870 (fertilizantes) y un peer es SIC 2873 (nitrógeno) — diferente
    sub-sector, comparación apples-to-oranges. SIC code prefix (2 dígitos)
    captura el nivel de industria; comparación por prefix evita warnings
    espurios cuando los códigos exactos difieren pero pertenecen al mismo
    grupo industrial.

    Returns: (sic_code, industry_name) o (None, None) si fetch falla.
    """
    try:
        from edgar import Company, set_identity  # type: ignore[import]
        set_identity(settings.sec_user_agent)
        company = Company(ticker)
        sic = str(getattr(company, "sic", "") or "") or None
        industry = str(getattr(company, "industry", "") or "")
        return sic, industry
    except Exception as e:
        log.debug("peers_sic_fetch_failed", ticker=ticker, error=str(e))
        return None, None


def _build_peer_valuations(reports: list) -> list[PeerValuation]:
    """
    Build PeerValuation list from FinancialsReport objects + Polygon market data. MCE-18.

    For each peer: fetches market_cap via Polygon, computes EV/EBIT and FCF yield.
    Graceful degradation: no Polygon data -> no_market_data=True, multiples=None.
    Peers with EBIT <= 0 get ev_ebit=None. Peers with FCF <= 0 get fcf_yield_pct=None.
    mf_rank is always None here — set by valuation_node after the full set is known.

    Args:
        reports: List of FinancialsReport objects (successfully parsed peers).

    Returns:
        List of PeerValuation, one per peer that has a roic_series entry.
    """
    result: list[PeerValuation] = []

    for rep in reports:
        if not rep.roic_series:
            log.warning("peer_valuation_skipped_no_roic", peer=rep.ticker)
            continue

        roic = rep.roic_series[0].value
        period = rep.roic_series[0].period

        try:
            mkt = PolygonAdapter.fetch_market_data(rep.ticker)
        except Exception as exc:
            log.warning("peer_valuation_polygon_failed", peer=rep.ticker, error=str(exc))
            result.append(PeerValuation(ticker=rep.ticker, roic=roic, period=period, no_market_data=True))
            continue
        market_cap: float | None = mkt.get("market_cap")

        if market_cap is None or market_cap <= 0:
            result.append(PeerValuation(
                ticker=rep.ticker,
                roic=roic,
                period=period,
                no_market_data=True,
            ))
            log.warning("peer_valuation_no_market_data", peer=rep.ticker)
            continue

        ebit: float | None = rep.operating_income[0].value if rep.operating_income else None
        fcf: float | None = rep.free_cash_flow[0].value if rep.free_cash_flow else None
        debt: float = rep.total_debt.value if rep.total_debt is not None else 0.0
        # cash_and_equivalents es SOLO caja desde H1 (EC-81); STI/LTI/NCI/preferentes van aparte.
        cash: float = rep.cash_and_equivalents.value if rep.cash_and_equivalents is not None else 0.0

        # EV del peer con la MISMA definición de derechos que el bridge del sujeto (ADR-033 §5):
        # si sujeto y peers usaran "net debt" distintos, la comparación EV/EBIT vs mediana de
        # peers (ADR-030) estaría sesgada. Market cap del peer (Polygon) como equity de mercado.
        lt_inv = _opt_value(rep, "long_term_investments")
        eq_method = _opt_value(rep, "equity_method_investments")
        if eq_method:
            lt_inv = (lt_inv or 0.0) + eq_method
        ev = market_cap + net_claims(
            debt, cash,
            sti=_opt_value(rep, "short_term_investments"),
            lt_investments=lt_inv,
            minority_interest=_opt_value(rep, "minority_interest"),
            preferred=_opt_value(rep, "preferred_stock"),
        )

        if ev <= 0:
            log.warning("peer_valuation_negative_ev", peer=rep.ticker, ev=ev)
        ev_ebit: float | None = None
        if ebit is not None and ebit > 0 and ev > 0:
            ev_ebit = round(ev / ebit, 2)

        fcf_yield_pct: float | None = None
        if fcf is not None and fcf > 0:
            fcf_yield_pct = round(fcf / market_cap * 100, 2)

        result.append(PeerValuation(
            ticker=rep.ticker,
            roic=roic,
            period=period,
            ev_ebit=ev_ebit,
            fcf_yield_pct=fcf_yield_pct,
        ))
        log.info("peer_valuation_built", peer=rep.ticker, ev_ebit=ev_ebit, fcf_yield_pct=fcf_yield_pct)

    return result


def peers_node(state: AnalysisState) -> dict:
    """
    Fetcha FinancialsReport para cada peer y actualiza competitive.peer_roic_comparison.

    Returns:
        Dict con "peer_financials" y, si hay datos de ROIC, "competitive" actualizado
        con peer_roic_comparison poblado. competitive_agent ya corrió — usamos
        model_copy para añadir los peers sin reescribir el resto del report.
    """
    peers: list[str] = state.get("peers", []) or []  # type: ignore[typeddict-item]

    if not peers:
        log.info("peers_node_skipped", reason="no peers provided")
        return {"peer_financials": [], "peers_excluded": [], "peers_cross_industry": []}

    # SIC validation (EC-42): comparar industria del subject vs cada peer.
    # Si los 2 primeros dígitos del SIC difieren, los negocios pertenecen a
    # subsectores distintos — comparación apples-to-oranges. Surfacear warning
    # al Value Expert para que lo refleje en §2 Moat Analysis.
    subject_ticker = str(state.get("ticker", "") or "")  # type: ignore[typeddict-item]
    subject_sic, subject_industry = _get_sic(subject_ticker) if subject_ticker else (None, None)
    sic_mismatches: list[str] = []

    # Primera pasada: SIC de cada peer. Un SIC desconocido (None) NO cuenta como discrepancia:
    # sin dato no se puede afirmar que sea de otra industria.
    sics = {p: _get_sic(p) for p in peers}
    mismatched = [
        p for p in peers
        if subject_sic and sics[p][0] and len(subject_sic) >= 2 and len(sics[p][0]) >= 2  # type: ignore[arg-type]
        and subject_sic[:2] != sics[p][0][:2]  # type: ignore[index]
    ]
    same_industry = [p for p in peers if p not in mismatched]
    # Para EXCLUIR hace falta confirmar la misma industria: un SIC desconocido no cuenta como "mismo"
    # (revisión del bloque C: 1 confirmado + 1 desconocido bastaban para descartar a 3).
    confirmed_same = [p for p in same_industry
                      if subject_sic and sics[p][0] and sics[p][0][:2] == subject_sic[:2]]  # type: ignore[index]

    # Política de exclusión (revisión ACMR: Finnhub sugirió AMKR, ENPH... y el 5-peer set mezclaba
    # industrias). Solo se EXCLUYEN peers sugeridos automáticamente (peers_source="auto"): el
    # inversor aceptó con Enter sin juzgarlos uno a uno. Un peer escrito por él es su criterio
    # (puede querer una comparación cross-industry a propósito) → se mantiene y solo se marca.
    # Y solo si quedan >= MIN_SAME_INDUSTRY_PEERS: con 0-1 peers una mediana no es comparación,
    # así que se mantienen todos y la comparación se etiqueta cross-industry (no dejar al
    # inversor sin nada).
    auto = (state.get("peers_source") or "user") == "auto"  # type: ignore[typeddict-item]
    exclude = auto and bool(mismatched) and len(confirmed_same) >= MIN_SAME_INDUSTRY_PEERS
    for p in mismatched:
        peer_sic, peer_industry = sics[p]
        sic_mismatches.append(
            f"{p} (SIC {peer_sic} — {peer_industry}) vs "
            f"{subject_ticker} (SIC {subject_sic} — {subject_industry}): "
            f"diferente industria — peer no apples-to-apples"
            + ("; excluded from the peer medians" if exclude else "; kept (cross-industry comparison)")
        )
        log.warning("peers_sic_mismatch", peer=p, peer_sic=peer_sic, subject_sic=subject_sic,
                    excluded=exclude)
    if exclude:
        # Los excluidos no se descargan: ni medianas, ni ranking Magic Formula (valuation.py solo ve
        # peer_valuations), ni comparación de ROIC. Quedan trazados en peer_sic_mismatches.
        peers = same_industry

    reports = []
    for peer_ticker in peers:
        try:
            raw = EdgarAdapter.fetch_raw(peer_ticker)
            rep = EdgarAdapter.parse_financials(raw)
            reports.append(rep)
            log.info("peers_node_fetched", peer=peer_ticker)
        except DataUnavailableError as e:
            log.warning("peers_node_peer_unavailable", peer=peer_ticker, error=str(e))
        except Exception as e:
            log.warning("peers_node_peer_failed", peer=peer_ticker, error=str(e))

    log.info("peers_node_complete", total_peers=len(peers), fetched=len(reports))

    out: dict = {"peer_financials": reports}
    if sic_mismatches:
        out["peer_sic_mismatches"] = sic_mismatches
    # Estado estructurado para el informe (report_facts.relative_section): quién se excluyó por
    # industria y quién sigue en una comparación cross-industry. Antes eran marcas de texto.
    out["peers_excluded"] = mismatched if exclude else []
    out["peers_cross_industry"] = [] if exclude else mismatched

    # Construir peer_roic_comparison desde los peers fetched con éxito.
    peer_roic: list[PeerROIC] = []
    for rep in reports:
        if rep.roic_series:
            # Hasta 3 periodos (más reciente primero) para mostrar tendencia.
            # Permite distinguir un ROIC estable, en mejora o cíclico (CALM: 52%→35%→18%).
            trail = [
                {"period": mv.period, "roic": mv.value}
                for mv in rep.roic_series[:3]
            ]
            peer_roic.append(PeerROIC(
                ticker=rep.ticker,
                roic=trail[0]["roic"],
                period=trail[0]["period"],
                roic_trail=trail,
            ))
        else:
            log.warning("peers_node_peer_no_roic", peer=rep.ticker,
                        hint="XBRL may lack invested_capital or operating_income for this ticker")

    competitive = state.get("competitive")  # type: ignore[typeddict-item]
    if peer_roic and competitive is not None and not competitive.stub:
        out["competitive"] = competitive.model_copy(update={"peer_roic_comparison": peer_roic})
        log.info("peers_node_roic_injected", peers=len(peer_roic))
    elif reports and not peer_roic:
        # Peers fetched pero ninguno tiene ROIC — guardar en state para que el archiver
        # pueda mostrar un mensaje honesto ("attempted but no ROIC data")
        fetched_tickers = [r.ticker for r in reports]
        log.warning("peers_node_no_roic_data", fetched=fetched_tickers,
                    hint="Peers fetched but none had computable ROIC from XBRL")
        out["peers_attempted_no_roic"] = fetched_tickers

    # Build peer_valuations (MCE-18): EV/EBIT, FCF yield via Polygon market data.
    # mf_rank is None here — set by valuation_node once the full subject+peer set is known.
    if reports:
        peer_valuations = _build_peer_valuations(reports)
        if peer_valuations:
            out["peer_valuations"] = peer_valuations
            log.info("peer_valuations_built", count=len(peer_valuations))

    return out
