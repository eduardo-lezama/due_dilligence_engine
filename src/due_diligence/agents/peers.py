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

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.schemas.reports import PeerROIC
from due_diligence.tools.edgar import DataUnavailableError, EdgarAdapter

log = structlog.get_logger()


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
        return {"peer_financials": []}

    # SIC validation (EC-42): comparar industria del subject vs cada peer.
    # Si los 2 primeros dígitos del SIC difieren, los negocios pertenecen a
    # subsectores distintos — comparación apples-to-oranges. Surfacear warning
    # al Value Expert para que lo refleje en §2 Moat Analysis.
    subject_ticker = str(state.get("ticker", "") or "")  # type: ignore[typeddict-item]
    subject_sic, subject_industry = _get_sic(subject_ticker) if subject_ticker else (None, None)
    sic_mismatches: list[str] = []

    reports = []
    for peer_ticker in peers:
        # Validar SIC match (EC-42) — fetch SIC primero para diagnóstico, luego raw para datos
        peer_sic, peer_industry = _get_sic(peer_ticker)
        if subject_sic and peer_sic and len(subject_sic) >= 2 and len(peer_sic) >= 2:
            if subject_sic[:2] != peer_sic[:2]:
                sic_mismatches.append(
                    f"{peer_ticker} (SIC {peer_sic} — {peer_industry}) vs "
                    f"{subject_ticker} (SIC {subject_sic} — {subject_industry}): "
                    f"diferente industria — peer no apples-to-apples"
                )
                log.warning(
                    "peers_sic_mismatch",
                    peer=peer_ticker,
                    peer_sic=peer_sic,
                    subject_sic=subject_sic,
                )

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

    return out
