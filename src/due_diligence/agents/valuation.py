"""
Valuation Agent — cálculo de valor intrínseco sin LLM.

Qué hace este módulo:
    Implementa el nodo LangGraph `valuation_agent` que cubre la sección §6
    (Valuation) del checklist canónico. Es un agente tool-lane: toda la aritmética
    la hace Python, no el LLM. Zero hallucination numérica por diseño (ADR-005).

Fuentes de datos:
    - state["financials"] (FinancialsReport) → FCF base, EBIT, net income, debt, cash
    - state["market"] (MarketReport) → precio actual, market cap
    - state["checkpoint2_user_input"] → WACC, terminal growth, proyección en años

Qué calcula:
    1. DCF en dos etapas (near-term + terminal) → equity value total
    2. DCF upside/downside vs market cap actual
    3. Reverse DCF → growth implícito en el precio actual (búsqueda binaria)
    4. Múltiplos: EV/EBIT, EV/FCF, P/E
    5. Earnings yield (EBIT/EV) — primera componente de Magic Formula (Greenblatt)
    6. Tabla de sensibilidad WACC × near-term growth (3×3)

Por qué near-term growth se calcula desde FCF histórico y no desde revenue:
    Ya tenemos el FCF calculado en FinancialsReport. Proyectar FCF directamente
    evita construir un modelo completo de márgenes — más simple y menos propenso
    a errores en Phase 1. El usuario puede override con revenue_growth_yr1_pct.

Por qué no hay LLM aquí:
    Los números de valoración deben ser deterministas y auditables. Si el LLM
    generara el DCF, no podríamos reproducirlo ni auditar el cálculo. Un número
    financiero que "calculó el LLM" viola ADR-005 y el principio de zero
    hallucination numérica de este sistema.
"""

from __future__ import annotations

from typing import Optional

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import ValuationInput, ValuationReport, PeerValuation

log = structlog.get_logger()

# Caps para near-term growth — evitan proyecciones absurdas si el FCF histórico
# es ruidoso (p.ej. un año con FCF anormalmente bajo infla el CAGR calculado).
_MAX_NEAR_TERM_GROWTH = 0.30   # 30% máximo
_MIN_NEAR_TERM_GROWTH = -0.15  # -15% mínimo


def _fcf_cagr(fcf_series: list[MetricValue]) -> Optional[float]:
    """
    Calcula el CAGR del FCF desde la serie histórica.

    Requiere ≥ 2 puntos con el más antiguo > 0 (FCF negativo hace el CAGR sin sentido).
    Devuelve None si los datos no son suficientes o válidos.
    """
    if len(fcf_series) < 2:
        return None
    newest = fcf_series[0].value
    oldest = fcf_series[-1].value
    if oldest <= 0 or newest <= 0:
        return None
    n = len(fcf_series) - 1
    return (newest / oldest) ** (1 / n) - 1


def _two_stage_dcf(
    base_fcf: float,
    near_term_growth: float,
    terminal_growth: float,
    wacc: float,
    years: int,
) -> float:
    """
    DCF en dos etapas: crecimiento near-term durante `years` años,
    luego perpetuidad con `terminal_growth`.

    Returns:
        Equity value total (USD). Suma de FCFs descontados + PV del terminal value.
    """
    # Asegurar que wacc > terminal_growth para que el denominador de la perpetuidad sea positivo
    if wacc <= terminal_growth:
        terminal_growth = wacc - 0.005  # 0.5% de margen mínimo

    pv = 0.0
    fcf = base_fcf
    for t in range(1, years + 1):
        fcf *= 1 + near_term_growth
        pv += fcf / (1 + wacc) ** t

    # Terminal value: Gordon Growth Model aplicado al FCF del año final
    terminal_fcf = fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv += terminal_value / (1 + wacc) ** years

    return pv


def _reverse_dcf(
    base_fcf: float,
    target_value: float,
    terminal_growth: float,
    wacc: float,
    years: int,
) -> Optional[float]:
    """
    Búsqueda binaria del near-term growth que iguala el DCF al target_value.

    Dado el market cap actual como target, responde: ¿qué crecimiento está
    implícito en el precio? Si el FCF base es negativo o el target es 0, retorna None.

    Returns:
        Growth rate implícito (fracción, ej 0.12 = 12%) o None si no calculable.
    """
    if base_fcf <= 0 or target_value <= 0:
        return None

    lo, hi = -0.50, 2.00  # rango: -50% a +200% growth anual
    for _ in range(60):   # 60 iteraciones da precisión de ~4 decimal places
        mid = (lo + hi) / 2
        val = _two_stage_dcf(base_fcf, mid, terminal_growth, wacc, years)
        if val < target_value:
            lo = mid
        else:
            hi = mid

    result = (lo + hi) / 2
    return round(result, 4)


def _normalized_fcf_proxy(ebit: float, net_income: Optional[float]) -> Optional[float]:
    """
    Proxy de cash generation normalizado cuando el FCF reportado es negativo (EC-13).

    Cuándo se usa: empresas en trough cíclico (MOS FY2025: FCF -$535M pero EBIT $822M)
    o en heavy reinvestment (growth-stage). Sin este fallback, el DCF se salta y el
    sistema devuelve "N/A — FCF insufficient", lo que pierde toda valoración para una
    clase entera del universo value (cyclicals descontados, compounders en inversión).

    Cálculo: NOPAT = EBIT × (1 − effective_tax_rate).
        - Si net_income también disponible y NI < EBIT: derive tax rate de la diferencia
          (proxy razonable que ignora interest expense — OK para análisis directional).
        - Si no: usar 25% default (mediana US corporate effective tax post-TCJA).

    Limitación: ignora working capital y maintenance vs growth capex. Sobreestima FCF
    sostenible para cyclicals en trough (cuando capex de mantenimiento es alto). Se
    documenta en `dcf_reliability_note` para que el lector lo trate como directional.

    Args:
        ebit: Operating income (EBIT) en USD, periodo más reciente.
        net_income: Net income en USD (para derivar tax rate), opcional.

    Returns:
        NOPAT proxy en USD, o None si EBIT no disponible/no-positivo.
    """
    if ebit is None or ebit <= 0:
        return None
    if net_income is not None and net_income > 0 and ebit > net_income:
        # Derive tax rate del gap EBIT→NI (ignora interest expense — OK proxy)
        derived_rate = 1 - (net_income / ebit)
        tax_rate = max(0.10, min(derived_rate, 0.40))  # clamp realista
    else:
        tax_rate = 0.25  # US corporate post-TCJA mediana
    return ebit * (1 - tax_rate)


def _sensitivity_table(
    base_fcf: float,
    terminal_growth: float,
    years: int,
    wacc_center: float,
    growth_center: float,
) -> list[dict]:
    """
    Tabla 3×3: eje X = WACC ± 1%, eje Y = near-term growth ± 5%.

    Cada celda es el equity value total del DCF. Permite ver el rango de
    valores razonables sin tener que re-ejecutar el análisis.
    """
    rows = []
    for g_delta in (0.05, 0.0, -0.05):
        g = growth_center + g_delta
        row = {"near_term_growth_pct": round((g) * 100, 1), "values": {}}
        for w_delta in (-0.01, 0.0, 0.01):
            w = wacc_center + w_delta
            val = _two_stage_dcf(base_fcf, g, terminal_growth, w, years)
            row["values"][f"wacc_{round(w*100,1)}pct"] = round(val / 1e9, 1)  # en USD B
        rows.append(row)
    return rows


def _compute_magic_formula_rank(
    subject_ticker: str,
    subject_earnings_yield: Optional[float],
    subject_roic: Optional[float],
    peer_valuations: list[PeerValuation],
) -> tuple[Optional[int], list[PeerValuation]]:
    """
    Compute Magic Formula rank (Greenblatt) for subject and all valid peers.

    Algorithm: rank by earnings yield (EBIT/EV, higher=better) + rank by ROIC
    (higher=better). Combined score = sum of both ranks (lower=better). Returns
    (subject_rank, updated_peer_valuations_with_mf_rank_filled).

    Requires >= 2 valid participants; returns (None, unchanged_peers) otherwise.
    Excluded: peers with no_market_data=True or ev_ebit=None or roic=None; subject with
    earnings_yield=None or roic=None.
    Ties in combined score are broken by input order (Python stable sort) — no deterministic secondary key.
    """
    participants: list[dict] = []

    # Add subject if it has valid data for both ranking components
    if (
        subject_earnings_yield is not None
        and subject_earnings_yield > 0
        and subject_roic is not None
    ):
        participants.append({
            "ticker": subject_ticker,
            "earnings_yield": subject_earnings_yield,  # percentage, higher = better
            "roic": subject_roic,                       # ratio, higher = better
            "is_subject": True,
        })

    # Add peers that have valid EV/EBIT (ev_ebit > 0, market data available)
    for pv in peer_valuations:
        if pv.no_market_data or pv.ev_ebit is None or pv.ev_ebit <= 0 or pv.roic is None:
            # roic=None guard: defensive for future Optional[float] schema changes
            continue
        # Convert EV/EBIT to earnings yield: earnings_yield = 100 / ev_ebit
        participants.append({
            "ticker": pv.ticker,
            "earnings_yield": round(100.0 / pv.ev_ebit, 4),
            "roic": pv.roic,
            "is_subject": False,
        })

    if len(participants) < 2:
        return None, peer_valuations

    # Rank by earnings yield descending (rank 1 = highest earnings yield = cheapest)
    by_ey = sorted(participants, key=lambda x: x["earnings_yield"], reverse=True)
    for i, p in enumerate(by_ey):
        p["ey_rank"] = i + 1

    # Rank by ROIC descending (rank 1 = highest ROIC = best quality)
    by_roic = sorted(participants, key=lambda x: x["roic"], reverse=True)
    for i, p in enumerate(by_roic):
        p["roic_rank"] = i + 1

    # Combined score — lower is better (Greenblatt)
    for p in participants:
        p["combined"] = p["ey_rank"] + p["roic_rank"]

    by_combined = sorted(participants, key=lambda x: x["combined"])
    for i, p in enumerate(by_combined):
        p["mf_rank"] = i + 1

    # Subject rank
    subject_rank: Optional[int] = next(
        (p["mf_rank"] for p in by_combined if p["is_subject"]),
        None,
    )

    # Build map of peer ranks by ticker
    peer_rank_map = {
        p["ticker"]: p["mf_rank"]
        for p in by_combined
        if not p["is_subject"]
    }

    # Return updated peer_valuations with mf_rank filled for valid participants
    updated: list[PeerValuation] = [
        pv.model_copy(update={"mf_rank": peer_rank_map[pv.ticker]})
        if pv.ticker in peer_rank_map
        else pv
        for pv in peer_valuations
    ]

    log.info(
        "magic_formula_rank_computed",
        subject=subject_ticker,
        subject_rank=subject_rank,
        participants=len(participants),
    )

    return subject_rank, updated


def valuation_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: calcula valor intrínseco usando aritmética pura.

    Lee FinancialsReport y MarketReport del estado + assumptions del usuario
    (checkpoint2_user_input). Si algún dato no está disponible, produce un
    ValuationReport parcial en lugar de fallar.

    Returns:
        {"valuation": ValuationReport(stub=False, ...)}
    """
    ticker = state["ticker"]
    financials = state.get("financials")   # type: ignore[typeddict-item]
    market = state.get("market")           # type: ignore[typeddict-item]
    user_raw: dict = state.get("checkpoint2_user_input") or {}  # type: ignore[typeddict-item]

    log.info("valuation_agent_start", ticker=ticker)

    # --- 1. Construir ValuationInput desde checkpoint2_user_input + defaults ---
    assumptions = ValuationInput(
        checkpoint_id="checkpoint2",
        wacc_pct=float(user_raw.get("wacc_pct", 9.0)),
        terminal_growth_pct=float(user_raw.get("terminal_growth_pct", 2.5)),
        projection_years=int(user_raw.get("projection_years", 10)),
        revenue_growth_yr1_pct=user_raw.get("revenue_growth_yr1_pct"),
        margin_assumption_pct=user_raw.get("margin_assumption_pct"),
    )
    wacc = assumptions.wacc_pct / 100
    terminal_growth = assumptions.terminal_growth_pct / 100
    years = assumptions.projection_years

    # --- 2. Extraer métricas del FinancialsReport ---
    base_fcf: Optional[float] = None
    ebit: Optional[float] = None
    net_income: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    fcf_period: str = "N/A"

    if financials is not None:
        if financials.free_cash_flow:
            base_fcf = financials.free_cash_flow[0].value
            fcf_period = financials.free_cash_flow[0].period
        if financials.operating_income:
            ebit = financials.operating_income[0].value
        if financials.net_income:
            net_income = financials.net_income[0].value
        if financials.total_debt is not None:
            total_debt = financials.total_debt.value
        if financials.cash_and_equivalents is not None:
            cash = financials.cash_and_equivalents.value

    # --- 3. Extraer market cap y precio del MarketReport ---
    market_cap: Optional[float] = None
    price_current_mv: Optional[MetricValue] = None
    as_of_date: str = "N/A"
    shares: Optional[float] = None
    if market is not None and market.market_cap is not None:
        market_cap = market.market_cap.value
        if market.market_cap.period:
            as_of_date = market.market_cap.period
    if market is not None and market.price_current is not None:
        price_current_mv = market.price_current

    # Shares outstanding: desde FinancialsReport (EDGAR XBRL) si disponible,
    # o derivado de market_cap / price como aproximación.
    if financials is not None and financials.shares_outstanding is not None:
        shares = financials.shares_outstanding.value
    elif market_cap is not None and price_current_mv is not None and price_current_mv.value > 0:
        shares = market_cap / price_current_mv.value

    # --- 4. Near-term growth: user override > min(FCF_CAGR, revenue_CAGR) > default 7% ---
    # ADR-022: se ancla en el mínimo de FCF CAGR y revenue CAGR.
    # FCF es más volátil que revenue (working capital, timing de capex) — usar solo
    # FCF CAGR sobreestima el growth sostenible para small caps con FCF ruidoso.
    # El revenue CAGR actúa como techo natural: el FCF no puede crecer indefinidamente
    # más rápido que el revenue sin compresión de márgenes.
    raw_cagr: Optional[float] = None
    revenue_cagr: Optional[float] = state.get("historical_cagr_5y")  # type: ignore[assignment]
    if assumptions.revenue_growth_yr1_pct is not None:
        near_term_growth = assumptions.revenue_growth_yr1_pct / 100
    elif financials is not None and financials.free_cash_flow:
        raw_cagr = _fcf_cagr(financials.free_cash_flow)
        if raw_cagr is not None:
            if revenue_cagr is not None and raw_cagr > revenue_cagr:
                # FCF CAGR supera el revenue CAGR — usar revenue como techo defensivo
                log.info(
                    "valuation_fcf_cagr_capped_by_revenue",
                    ticker=ticker,
                    fcf_cagr=f"{raw_cagr:.1%}",
                    revenue_cagr=f"{revenue_cagr:.1%}",
                )
                near_term_growth = revenue_cagr
            else:
                near_term_growth = raw_cagr
        else:
            near_term_growth = revenue_cagr if revenue_cagr is not None else 0.07
    else:
        near_term_growth = revenue_cagr if revenue_cagr is not None else 0.07

    # Limitar a rango razonable para evitar proyecciones absurdas.
    # Guardar si se aplicó el cap — es señal de que el FCF histórico es anormal.
    growth_was_capped = near_term_growth > _MAX_NEAR_TERM_GROWTH or near_term_growth < _MIN_NEAR_TERM_GROWTH
    near_term_growth = max(_MIN_NEAR_TERM_GROWTH, min(_MAX_NEAR_TERM_GROWTH, near_term_growth))

    # Construir nota de fiabilidad del DCF.
    # Dos condiciones la activan (pueden combinarse):
    #   (a) growth capeado al 30% — FCF en pico cíclico.
    #   (b) forward DCF y reverse DCF contradicen en >5pp — upside es artefacto de
    #       assumptions que el mercado no paga. El reverse DCF es más informativo.
    dcf_reliability_note: Optional[str] = None
    note_parts: list[str] = []

    if growth_was_capped and raw_cagr is not None and assumptions.revenue_growth_yr1_pct is None:
        note_parts.append(
            f"⚠️ DCF growth capped at {_MAX_NEAR_TERM_GROWTH:.0%} "
            f"(raw FCF CAGR was {raw_cagr:.0%}). "
            f"The base FCF may be at a cyclical peak — forward DCF likely overstates intrinsic value."
        )
        log.warning("valuation_dcf_growth_capped", ticker=ticker, raw_cagr=f"{raw_cagr:.0%}")

    log.info(
        "valuation_agent_assumptions",
        ticker=ticker,
        wacc_pct=assumptions.wacc_pct,
        near_term_growth_pct=round(near_term_growth * 100, 1),
        terminal_growth_pct=assumptions.terminal_growth_pct,
        years=years,
    )

    # --- 5. DCF — usar FCF si positivo, fallback a NOPAT proxy si FCF<0 pero EBIT>0 (EC-13) ---
    # Empresas en trough cíclico (MOS FY2025: FCF -$535M / EBIT $822M) o heavy reinvestment
    # (growth-stage) tienen FCF negativo transitorio pero EBIT positivo. Sin este fallback,
    # el sistema devolvía "N/A — FCF insufficient" perdiendo toda valoración. Usar NOPAT
    # como proxy de cash generation normalizado preserva la valoración directional.
    dcf_base: Optional[float] = None
    dcf_base_source: str = ""  # etiqueta para `MetricValue.source` y reliability note

    if base_fcf is not None and base_fcf > 0:
        dcf_base = base_fcf
        dcf_base_source = "FCF"
    elif ebit is not None and ebit > 0:
        proxy = _normalized_fcf_proxy(ebit, net_income)
        if proxy is not None and proxy > 0:
            dcf_base = proxy
            dcf_base_source = "NOPAT proxy (EBIT × (1-tax))"

    dcf_value: Optional[MetricValue] = None
    dcf_per_share: Optional[MetricValue] = None
    dcf_upside: Optional[float] = None

    if dcf_base is not None and dcf_base > 0:
        equity_value = _two_stage_dcf(dcf_base, near_term_growth, terminal_growth, wacc, years)
        dcf_value = MetricValue(
            value=round(equity_value),
            unit="USD",
            period=fcf_period,
            source=f"calculated ({dcf_base_source})",
        )
        if shares is not None and shares > 0:
            dcf_per_share = MetricValue(
                value=round(equity_value / shares, 2),
                unit="USD/share",
                period=fcf_period,
                source=f"calculated ({dcf_base_source})",
            )
        if market_cap is not None and market_cap > 0:
            dcf_upside = round((equity_value - market_cap) / market_cap * 100, 1)
    else:
        log.warning("valuation_agent_no_dcf_base", ticker=ticker,
                    fcf=base_fcf, ebit=ebit)

    # --- 6. Reverse DCF — usa el mismo dcf_base (FCF o NOPAT proxy) ---
    reverse_growth: Optional[float] = None
    if dcf_base is not None and market_cap is not None:
        reverse_growth = _reverse_dcf(dcf_base, market_cap, terminal_growth, wacc, years)

    # DCF spread note (Fix C / EC-16): si el forward DCF asume un growth >5pp
    # por encima del implied growth, el upside existe solo por assumptions que el
    # mercado no está pagando. Añadir a la nota de fiabilidad.
    _CONTRADICTION_THRESHOLD = 0.05
    if (
        reverse_growth is not None
        and (near_term_growth - reverse_growth) > _CONTRADICTION_THRESHOLD
        and assumptions.revenue_growth_yr1_pct is None  # solo si no hay override del usuario
    ):
        spread_pp = (near_term_growth - reverse_growth) * 100
        note_parts.append(
            f"⚠️ DCF spread note: forward DCF uses {near_term_growth:.1%} near-term growth "
            f"(historical CAGR) but the market currently prices only {reverse_growth:.1%} "
            f"(spread: {spread_pp:.0f}pp). The DCF upside is conditional on the analyst's "
            f"growth assumption being correct vs what the market discounts — not a free margin "
            f"of safety. Reverse DCF is the more informative valuation anchor."
        )
        log.warning(
            "valuation_dcf_spread",
            ticker=ticker,
            forward_growth=f"{near_term_growth:.1%}",
            implied_growth=f"{reverse_growth:.1%}",
            spread_pp=f"{spread_pp:.0f}",
        )

    # Nota EC-13: cuando usamos NOPAT proxy en lugar de FCF, documentarlo explícitamente.
    # El lector debe saber que la valoración es directional, no point estimate.
    if dcf_base_source.startswith("NOPAT") and dcf_base is not None and base_fcf is not None:
        note_parts.append(
            f"⚠️ DCF computed on NORMALIZED NOPAT (${dcf_base / 1e6:.0f}M = EBIT × (1-tax)) "
            f"because reported FCF was negative (${base_fcf / 1e6:.0f}M in {fcf_period}). "
            f"This is appropriate for cyclical troughs or heavy reinvestment but ignores "
            f"working capital and maintenance vs growth capex split — treat as directional, "
            f"not point estimate. Reverse DCF implied growth uses same NOPAT base."
        )
        log.info(
            "valuation_dcf_nopat_fallback",
            ticker=ticker,
            fcf=base_fcf,
            ebit=ebit,
            nopat_proxy=dcf_base,
        )

    # Actualizar sensitivity table también si usamos NOPAT proxy
    if note_parts:
        dcf_reliability_note = " ".join(note_parts)

    # --- 7. Múltiplos (requieren market cap) ---
    ev_ebit_mv: Optional[MetricValue] = None
    ev_fcf_mv: Optional[MetricValue] = None
    pe_mv: Optional[MetricValue] = None
    earnings_yield: Optional[float] = None

    if market_cap is not None:
        # Enterprise Value = market cap + deuda neta
        debt_val = total_debt or 0.0
        cash_val = cash or 0.0
        ev = market_cap + debt_val - cash_val

        period_label = as_of_date

        if ebit is not None and ev > 0:
            ev_ebit_mv = MetricValue(
                value=round(ev / ebit, 2),
                unit="x",
                period=period_label,
                source="calculated",
            )
            earnings_yield = round(ebit / ev * 100, 2)  # EBIT/EV × 100

        if base_fcf is not None and base_fcf > 0 and ev > 0:
            ev_fcf_mv = MetricValue(
                value=round(ev / base_fcf, 2),
                unit="x",
                period=period_label,
                source="calculated",
            )

        if net_income is not None and net_income > 0 and market_cap > 0:
            pe_mv = MetricValue(
                value=round(market_cap / net_income, 2),
                unit="x",
                period=period_label,
                source="calculated",
            )

    # --- 8. Sensitivity table — usa dcf_base (FCF o NOPAT proxy según fallback EC-13) ---
    sensitivity: list[dict] = []
    if dcf_base is not None and dcf_base > 0:
        sensitivity = _sensitivity_table(dcf_base, terminal_growth, years, wacc, near_term_growth)

    # --- 9. Magic Formula rank (MCE-18) ---
    # peer_valuations populated by peers_node; mf_rank=None there, computed here.
    peer_valuations_raw: list[PeerValuation] = list(
        state.get("peer_valuations") or []  # type: ignore[typeddict-item]
    )
    subject_roic_for_mf: Optional[float] = None
    if financials is not None and financials.roic_series:
        subject_roic_for_mf = financials.roic_series[0].value

    mf_rank, updated_peer_valuations = _compute_magic_formula_rank(
        subject_ticker=ticker,
        subject_earnings_yield=earnings_yield,
        subject_roic=subject_roic_for_mf,
        peer_valuations=peer_valuations_raw,
    )

    # --- 10. Construir el report ---
    report = ValuationReport(
        ticker=ticker,
        stub=False,
        dcf_intrinsic_value=dcf_value,
        dcf_intrinsic_per_share=dcf_per_share,
        price_current=price_current_mv,
        dcf_upside_pct=dcf_upside,
        reverse_dcf_implied_growth=reverse_growth,
        ev_ebit_multiple=ev_ebit_mv,
        ev_fcf_multiple=ev_fcf_mv,
        pe_multiple=pe_mv,
        earnings_yield_pct=earnings_yield,
        magic_formula_rank=mf_rank,
        sensitivity_table=sensitivity,
        assumptions=assumptions,
        dcf_reliability_note=dcf_reliability_note,
    )

    log.info(
        "valuation_agent_complete",
        ticker=ticker,
        dcf_upside_pct=dcf_upside,
        reverse_implied_growth=reverse_growth,
        ev_ebit=ev_ebit_mv.value if ev_ebit_mv else None,
        pe=pe_mv.value if pe_mv else None,
    )

    result: dict = {"valuation": report}
    if updated_peer_valuations:
        result["peer_valuations"] = updated_peer_valuations
    return result
