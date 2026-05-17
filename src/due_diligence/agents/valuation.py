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


def _three_stage_dcf(
    base_fcf: float,
    g1: float,
    terminal_growth: float,
    wacc: float,
    stage1_years: int,
    fade_years: int = 0,
) -> float:
    """
    DCF en tres etapas — Damodaran fade method (ADR-029).

    Stage 1 (años 1..stage1_years): crecimiento constante a g1.
    Stage 2 (años stage1_years+1..stage1_years+fade_years): fade lineal g1 → terminal_growth.
    Stage 3: perpetuidad a terminal_growth (Gordon Growth Model).

    fade_years=0 degrada al 2-stage clásico (compatibilidad retroactiva).
    El fade reconoce que mantener g1 durante una década completa es menos probable
    que normalizar hacia el crecimiento de la economía tras los primeros años de ventaja.

    Returns:
        Equity value total (USD). Suma de FCFs descontados + PV del terminal value.
    """
    # Asegurar wacc > terminal_growth para evitar denominador negativo o infinito
    if wacc <= terminal_growth:
        terminal_growth = wacc - 0.005

    pv = 0.0
    fcf = base_fcf

    # Stage 1: g1 constante
    for t in range(1, stage1_years + 1):
        fcf *= 1 + g1
        pv += fcf / (1 + wacc) ** t

    # Stage 2: fade lineal de g1 → terminal_growth
    # g_t en fade_t-ésimo año = g1 - (g1 - terminal_growth) * fade_t / fade_years
    for fade_t in range(1, fade_years + 1):
        g_t = g1 - (g1 - terminal_growth) * fade_t / fade_years
        fcf *= 1 + g_t
        t = stage1_years + fade_t
        pv += fcf / (1 + wacc) ** t

    # Stage 3: perpetuidad a terminal_growth (Gordon Growth Model)
    total_explicit = stage1_years + fade_years
    terminal_fcf = fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv += terminal_value / (1 + wacc) ** total_explicit

    return pv


def _two_stage_dcf(
    base_fcf: float,
    near_term_growth: float,
    terminal_growth: float,
    wacc: float,
    years: int,
) -> float:
    """
    Wrapper de compatibilidad — delega a _three_stage_dcf con fade_years=0.

    Mantiene la firma original para que cualquier test o código externo que llame
    _two_stage_dcf directamente siga funcionando sin cambios.
    """
    return _three_stage_dcf(
        base_fcf, near_term_growth, terminal_growth, wacc,
        stage1_years=years, fade_years=0,
    )


def _resolve_stages(
    projection_years: int,
    stage1_years: Optional[int],
    fade_years: Optional[int],
) -> tuple[int, int]:
    """
    Calcula stage1_years y fade_years con defaults inteligentes (ADR-029).

    Lógica:
    - Si el usuario especificó ambos: usarlos directamente.
    - Si especificó solo stage1: fade = projection_years - stage1.
    - Si no especificó nada: split 50/50 (stage1 = projection_years // 2).
    - Si la suma excede projection_years: recortar fade para respetar el total.

    El resultado siempre cumple stage1 >= 1 y fade >= 0.
    """
    if stage1_years is not None and fade_years is not None:
        return max(1, stage1_years), max(0, fade_years)
    if stage1_years is not None:
        s1 = max(1, stage1_years)
        return s1, max(0, projection_years - s1)
    # Auto 50/50: stage1 al menos 1
    s1 = max(1, projection_years // 2)
    return s1, projection_years - s1


def _reverse_dcf(
    base_fcf: float,
    target_value: float,
    terminal_growth: float,
    wacc: float,
    years: int,
    stage1_years: Optional[int] = None,
    fade_years: Optional[int] = None,
) -> Optional[float]:
    """
    Búsqueda binaria del g1 (stage 1 growth) que iguala el DCF al target_value.

    Dado el market cap actual como target, responde: ¿qué crecimiento implica
    el precio actual? Usa la misma estructura 3-stage que el forward DCF para
    que los dos valores sean comparables.

    Si stage1_years/fade_years son None, usa years como stage1 y fade=0
    (comportamiento idéntico al 2-stage original).

    Returns:
        Growth rate implícito (fracción, ej 0.12 = 12%) o None si no calculable.
    """
    if base_fcf <= 0 or target_value <= 0:
        return None

    s1 = stage1_years if stage1_years is not None else years
    fd = fade_years if fade_years is not None else 0

    lo, hi = -0.50, 2.00  # rango: -50% a +200% growth anual
    for _ in range(60):   # 60 iteraciones → precisión ~4 decimal places
        mid = (lo + hi) / 2
        val = _three_stage_dcf(base_fcf, mid, terminal_growth, wacc,
                                stage1_years=s1, fade_years=fd)
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


# --- Constantes WACC advisory (ADR-029) ---
# Rf: US 10y Treasury yield — actualizar manualmente cuando los tipos cambien significativamente.
_RF_PCT: float = 4.2    # as of 2026-05
_ERP_PCT: float = 5.0   # Damodaran historical ERP (US)
_BETA_DEFAULT: float = 1.0  # simplificación: β=1, sin cálculo de β específica de empresa


def _ebit_median(
    ebit_series: list,  # list[MetricValue]
    min_periods: int = 3,
) -> tuple[Optional[float], str]:
    """
    Mediana del EBIT en últimos periodos para normalización EPV (ADR-029).

    Greenwald usa EBIT "normalizado" (suavizado sobre el ciclo) para evitar que
    un pico o trough cíclico distorsione el EPV. La mediana es más robusta que
    la media ante outliers.

    Jerarquía: median_5y → median_Ny (≥3) → latest.

    Returns:
        (median_ebit_usd, source_label) donde source: "ebit_median_5y",
        "ebit_median_Ny", "ebit_latest", o (None, "unavailable").
    """
    if not ebit_series:
        return None, "unavailable"
    values = [mv.value for mv in ebit_series if mv.value is not None and mv.value > 0]
    if not values:
        return None, "unavailable"

    if len(values) >= 5:
        subset = values[:5]
        label = "ebit_median_5y"
    elif len(values) >= min_periods:
        subset = values[:len(values)]
        label = f"ebit_median_{len(values)}y"
    else:
        subset = values[:1]
        label = "ebit_latest"

    sorted_vals = sorted(subset)
    n = len(sorted_vals)
    med = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    return med, label


def _compute_epv(
    ebit_series: list,  # list[MetricValue]
    net_income_latest: Optional[float],
    wacc: float,
    net_debt: float,
    shares: Optional[float],
    market_cap: Optional[float],
) -> tuple:  # (Optional[MetricValue], Optional[MetricValue], Optional[float], Optional[str])
    """
    Earnings Power Value — Greenwald (ADR-029).

    EPV_enterprise = NOPAT_normalizado / WACC
    EPV_equity = EPV_enterprise - net_debt
    EPV_per_share = EPV_equity / shares

    EPV_enterprise es comparable con market_cap (ambos enterprise-level).
    EPV_per_share es comparable con market price (ambos equity-level).

    Tax rate derivada de NI/EBIT si disponible, else 25% (US corporate default).

    Returns:
        (epv_enterprise_mv, epv_per_share_mv, epv_upside_pct, source_label)
        Todos pueden ser None si no hay datos suficientes.
    """
    from due_diligence.schemas.base import MetricValue as _MV

    ebit_med, source = _ebit_median(ebit_series)
    if ebit_med is None or wacc <= 0:
        return None, None, None, None

    # Tax rate: derivar de NI/EBIT del último período si coherente
    ebit_latest = ebit_series[0].value if ebit_series else None
    if (
        net_income_latest is not None
        and net_income_latest > 0
        and ebit_latest is not None
        and ebit_latest > net_income_latest
    ):
        derived_tax = 1 - (net_income_latest / ebit_latest)
        tax_rate = max(0.10, min(derived_tax, 0.40))
    else:
        tax_rate = 0.25

    nopat = ebit_med * (1 - tax_rate)
    epv_enterprise = nopat / wacc

    period = ebit_series[0].period if ebit_series else "N/A"
    epv_ent_mv = _MV(
        value=round(epv_enterprise),
        unit="USD",
        period=period,
        source=f"EPV ({source})",
    )

    epv_ps_mv: Optional[_MV] = None
    epv_upside: Optional[float] = None

    if shares is not None and shares > 0:
        epv_equity = epv_enterprise - net_debt
        epv_per_share_val = epv_equity / shares
        epv_ps_mv = _MV(
            value=round(epv_per_share_val, 2),
            unit="USD/share",
            period=period,
            source=f"EPV ({source})",
        )

    if market_cap is not None and market_cap > 0:
        # upside enterprise-level: (EPV_enterprise - mkt_cap) / mkt_cap
        # mkt_cap sirve como proxy de enterprise value para este upside indicativo
        epv_upside = round((epv_enterprise - market_cap) / market_cap * 100, 1)

    return epv_ent_mv, epv_ps_mv, epv_upside, source


def _compute_suggested_wacc(
    market_cap: Optional[float],
    rf_pct: float = _RF_PCT,
    erp_pct: float = _ERP_PCT,
    beta: float = _BETA_DEFAULT,
) -> tuple[float, str]:
    """
    WACC advisory por CAPM simplificado (ADR-029).

    Fórmula: WACC = Rf + β × ERP + size_premium
    Con β=1 (simplificación — sin cálculo de beta específico por empresa).

    size_premium por market cap bucket (Damodaran 2024):
    >$10B (large cap):   0%
    $2-10B (mid cap):    0.5%
    $300M-$2B (small cap): 1.5%
    <$300M (micro cap):  3.0%

    Se presenta al usuario como "sugerencia" en Checkpoint 2.
    El usuario puede hacer override con el WACC que considere apropiado.

    Returns:
        (wacc_pct_float, breakdown_string) — ej (9.7, "Rf=4.2% + β=1.0×ERP=5.0% + size_premium=0.5% (mid cap)")
    """
    if market_cap is None or market_cap >= 10e9:
        size_label, size_prem = "large cap", 0.0
    elif market_cap >= 2e9:
        size_label, size_prem = "mid cap", 0.5
    elif market_cap >= 300e6:
        size_label, size_prem = "small cap", 1.5
    else:
        size_label, size_prem = "micro cap", 3.0

    suggested = rf_pct + beta * erp_pct + size_prem
    breakdown = (
        f"Rf={rf_pct}% + β={beta}×ERP={erp_pct}% + size_premium={size_prem}% ({size_label})"
    )
    return round(suggested, 1), breakdown


def _classify_dcf_upside(upside_pct: Optional[float]) -> str:
    """Clasifica upside DCF: >20% cheap, (-20,20) fair, <-20% expensive."""
    if upside_pct is None:
        return "n/a"
    if upside_pct > 20:
        return "cheap"
    if upside_pct < -20:
        return "expensive"
    return "fair"


def _classify_epv_upside(upside_pct: Optional[float]) -> str:
    """
    Clasifica EPV vs market cap.

    >10%: cheap (mercado paga menos que el valor sin crecimiento).
    0-10%: fair (mercado paga algo por encima del EPV pero poco).
    <0%: expensive (mercado valora el crecimiento; EPV no justifica el precio).
    """
    if upside_pct is None:
        return "n/a"
    if upside_pct > 10:
        return "cheap"
    if upside_pct < 0:
        return "expensive"
    return "fair"


def _classify_reverse_vs_historical(
    implied_growth: Optional[float],
    historical_cagr: Optional[float],
    threshold: float = 0.03,
) -> str:
    """
    Compara el growth implícito por el precio actual con el CAGR histórico.

    cheap: mercado descuenta menos crecimiento del que la empresa ha demostrado (±3pp).
    expensive: mercado asume más crecimiento del que la empresa ha generado históricamente.
    """
    if implied_growth is None or historical_cagr is None:
        return "n/a"
    spread = implied_growth - historical_cagr
    if spread < -threshold:
        return "cheap"
    if spread > threshold:
        return "expensive"
    return "fair"


def _classify_ev_ebit_vs_peers(
    subject_ev_ebit: Optional[float],
    peer_ev_ebit_median: Optional[float],
) -> str:
    """
    Compara EV/EBIT del sujeto con la mediana de sus peers.

    <0.7× mediana = cheap (descuento relevante vs comparables).
    >1.3× mediana = expensive (prima significativa sobre comparables).
    """
    if subject_ev_ebit is None or peer_ev_ebit_median is None or peer_ev_ebit_median <= 0:
        return "n/a"
    ratio = subject_ev_ebit / peer_ev_ebit_median
    if ratio < 0.70:
        return "cheap"
    if ratio > 1.30:
        return "expensive"
    return "fair"


def _build_methods_summary(
    dcf_per_share,      # Optional[MetricValue]
    dcf_upside_pct: Optional[float],
    epv_per_share,      # Optional[MetricValue]
    epv_upside_pct: Optional[float],
    reverse_growth: Optional[float],
    historical_cagr: Optional[float],
    ev_ebit,            # Optional[MetricValue]
    peer_ev_ebit_median: Optional[float],
) -> list[dict]:
    """
    Tabla de 4 métodos de valoración con clasificación uniforme (ADR-030).

    Cada fila: method, per_share, display_upside, classification.
    Consumida por Value Expert (_format_context) y Archiver.

    Los classifiers siguen umbrales simples: ver _classify_* functions.
    La síntesis de la convergencia la hace el LLM (Value Expert regla #21)
    en lugar de determinizarse en código para preservar la division tool/LLM lane.
    """
    def _fmt_usd(v: Optional[float]) -> str:
        return f"${v:.2f}" if v is not None else "N/A"

    def _fmt_pct(v: Optional[float]) -> str:
        return f"{v:.1%}" if v is not None else "N/A"

    def _fmt_x(v: Optional[float]) -> str:
        return f"{v:.1f}x" if v is not None else "N/A"

    ev_ebit_val = ev_ebit.value if ev_ebit is not None else None

    return [
        {
            "method": "DCF (3-stage Damodaran fade)",
            "per_share": _fmt_usd(dcf_per_share.value if dcf_per_share else None),
            "upside_pct": dcf_upside_pct,
            "display_upside": f"{dcf_upside_pct:+.1f}%" if dcf_upside_pct is not None else "N/A",
            "classification": _classify_dcf_upside(dcf_upside_pct),
        },
        {
            "method": "EPV (Greenwald — zero growth)",
            "per_share": _fmt_usd(epv_per_share.value if epv_per_share else None),
            "upside_pct": epv_upside_pct,
            "display_upside": f"{epv_upside_pct:+.1f}%" if epv_upside_pct is not None else "N/A",
            "classification": _classify_epv_upside(epv_upside_pct),
        },
        {
            "method": "Reverse DCF (market-implied growth)",
            "per_share": "N/A",
            "upside_pct": None,
            "display_upside": (
                f"implied {_fmt_pct(reverse_growth)} vs historical {_fmt_pct(historical_cagr)}"
            ),
            "classification": _classify_reverse_vs_historical(reverse_growth, historical_cagr),
        },
        {
            "method": "EV/EBIT vs peer median",
            "per_share": "N/A",
            "upside_pct": None,
            "display_upside": (
                f"{_fmt_x(ev_ebit_val)} vs peer median {_fmt_x(peer_ev_ebit_median)}"
            ),
            "classification": _classify_ev_ebit_vs_peers(ev_ebit_val, peer_ev_ebit_median),
        },
    ]


def _compute_near_term_growth(
    financials,  # Optional[FinancialsReport]
    revenue_cagr: Optional[float],
    user_override_pct: Optional[float],
) -> tuple[float, str]:
    """
    Calcula near_term_growth y su fuente sin efectos secundarios.

    Jerarquía:
        1. user_override_pct — el usuario manda siempre
        2. FCF CAGR (con cap al revenue_cagr si FCF > revenue) — señal más directa
        3. revenue_cagr — proxy cuando FCF CAGR no disponible (FCF negativo/insuficiente)
        4. 7% — fallback genérico anclado en PIB nominal US + prima small/mid cap

    Returns:
        (near_term_growth, source) con los caps [-15%, 30%] ya aplicados.
        source values: "user_override" | "fcf_cagr_Ny" | "fcf_cagr_Ny_rev_capped" |
                       "revenue_cagr_fallback" | "default_7pct"

    Extraído de valuation_agent() como función pura para que el CLI pueda mostrar
    el valor auto antes del prompt de override (EC-54, Task 3).
    """
    raw_cagr: Optional[float] = None

    if user_override_pct is not None:
        growth = user_override_pct / 100
        source = "user_override"
    elif financials is not None and financials.free_cash_flow:
        raw_cagr = _fcf_cagr(financials.free_cash_flow)
        n_periods = max(1, len(financials.free_cash_flow) - 1)  # periodos de diferencia

        if raw_cagr is not None:
            if revenue_cagr is not None and raw_cagr > revenue_cagr:
                growth = revenue_cagr
                source = f"fcf_cagr_{n_periods}y_rev_capped"
            else:
                growth = raw_cagr
                source = f"fcf_cagr_{n_periods}y"
        else:
            # FCF negativo o insuficiente — caer al revenue CAGR o al default
            growth = revenue_cagr if revenue_cagr is not None else 0.07
            source = "revenue_cagr_fallback" if revenue_cagr is not None else "default_7pct"
    else:
        growth = revenue_cagr if revenue_cagr is not None else 0.07
        source = "revenue_cagr_fallback" if revenue_cagr is not None else "default_7pct"

    growth = max(_MIN_NEAR_TERM_GROWTH, min(_MAX_NEAR_TERM_GROWTH, growth))
    return growth, source


def _sensitivity_table(
    base_fcf: float,
    terminal_growth: float,
    years: int,
    wacc_center: float,
    growth_center: float,
    stage1_years: int = 0,
    fade_years: int = 0,
) -> list[dict]:
    """
    Tabla 3×3: eje X = WACC ± 1%, eje Y = g1 (stage 1 growth) ± 5%.

    Cada celda es el equity value total del DCF 3-stage. Permite ver el rango de
    valores razonables sin tener que re-ejecutar el análisis. El fade se mantiene
    fijo en todas las celdas — solo g1 y WACC varían.

    stage1_years=0 + fade_years=0 equivale al comportamiento 2-stage original
    (stage1=years, fade=0) para compatibilidad en tests.
    """
    # Si no se pasan stages, usar el total de years como stage1 sin fade
    s1 = stage1_years if stage1_years > 0 else years
    fd = fade_years

    rows = []
    for g_delta in (0.05, 0.0, -0.05):
        g = growth_center + g_delta
        row = {"near_term_growth_pct": round(g * 100, 1), "values": {}}
        for w_delta in (-0.01, 0.0, 0.01):
            w = wacc_center + w_delta
            val = _three_stage_dcf(base_fcf, g, terminal_growth, w,
                                   stage1_years=s1, fade_years=fd)
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
        stage1_years=user_raw.get("stage1_years"),
        fade_years=user_raw.get("fade_years"),
        revenue_growth_yr1_pct=user_raw.get("revenue_growth_yr1_pct"),
        margin_assumption_pct=user_raw.get("margin_assumption_pct"),
    )
    wacc = assumptions.wacc_pct / 100
    terminal_growth = assumptions.terminal_growth_pct / 100
    years = assumptions.projection_years

    # 3-stage split: resolved from user input with auto 50/50 fallback (ADR-029)
    stage1_years, fade_years = _resolve_stages(
        years, assumptions.stage1_years, assumptions.fade_years
    )

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
    revenue_cagr: Optional[float] = state.get("historical_cagr_5y")  # type: ignore[assignment]
    near_term_growth, near_term_growth_source = _compute_near_term_growth(
        financials=financials,
        revenue_cagr=revenue_cagr,
        user_override_pct=assumptions.revenue_growth_yr1_pct,
    )

    # Detectar si se aplicaron caps (FCF_CAGR pre-cap puede calcularse desde el source)
    raw_cagr: Optional[float] = (
        _fcf_cagr(financials.free_cash_flow)
        if financials is not None and financials.free_cash_flow else None
    )
    growth_was_capped = (
        raw_cagr is not None
        and (raw_cagr > _MAX_NEAR_TERM_GROWTH or raw_cagr < _MIN_NEAR_TERM_GROWTH)
        and near_term_growth_source not in ("user_override", "revenue_cagr_fallback", "default_7pct")
    )

    if "rev_capped" in near_term_growth_source:
        log.info(
            "valuation_fcf_cagr_capped_by_revenue",
            ticker=ticker,
            fcf_cagr=f"{raw_cagr:.1%}" if raw_cagr is not None else "N/A",
            revenue_cagr=f"{revenue_cagr:.1%}" if revenue_cagr is not None else "N/A",
        )

    # --- Reliability note — se construye SIEMPRE con contexto del growth usado (EC-54) ---
    # El near_term_growth es el parámetro más sensible del DCF: mover de 8% a 15%
    # cambia el equity value un 30-50%. El usuario y el Value Expert deben saber siempre
    # qué valor se usó, de dónde viene, y cuán confiable es esa señal.
    dcf_reliability_note: Optional[str] = None
    note_parts: list[str] = []

    # Línea de contexto del growth — siempre en primer lugar, nivel de alerta según fuente
    if near_term_growth_source == "user_override":
        growth_context = (
            f"Near-term FCF growth: {near_term_growth:.1%} (user-specified override)."
        )
    elif near_term_growth_source == "default_7pct":
        growth_context = (
            f"⚠️ Near-term FCF growth: {near_term_growth:.1%} (generic fallback — no company-specific "
            f"historical data available). The DCF is not anchored to any empirical signal for this "
            f"company. Treat DCF upside as purely illustrative."
        )
    elif "rev_capped" in near_term_growth_source:
        n_y = near_term_growth_source.split("_")[2].replace("y", "")
        growth_context = (
            f"Near-term FCF growth: {near_term_growth:.1%} "
            f"(FCF CAGR {n_y}y, capped by revenue CAGR "
            f"{revenue_cagr:.1%} — FCF growing faster than revenue is unsustainable long-term)."
            if revenue_cagr is not None else
            f"Near-term FCF growth: {near_term_growth:.1%} "
            f"(FCF CAGR {n_y}y, capped at {_MAX_NEAR_TERM_GROWTH:.0%})."
        )
    elif near_term_growth_source == "revenue_cagr_fallback":
        growth_context = (
            f"⚠️ Near-term FCF growth: {near_term_growth:.1%} (revenue CAGR proxy — FCF CAGR "
            f"unavailable because reported FCF is negative or <2 periods). Less direct signal "
            f"than FCF CAGR."
        )
    else:  # fcf_cagr_Ny
        n_y = near_term_growth_source.split("_")[2].replace("y", "")
        growth_context = (
            f"Near-term FCF growth: {near_term_growth:.1%} (historical FCF CAGR, {n_y} years)."
        )
    note_parts.append(growth_context)

    if growth_was_capped and assumptions.revenue_growth_yr1_pct is None:
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
        equity_value = _three_stage_dcf(dcf_base, near_term_growth, terminal_growth, wacc,
                                         stage1_years=stage1_years, fade_years=fade_years)
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
    # Usa la misma estructura 3-stage para que forward y reverse sean comparables.
    reverse_growth: Optional[float] = None
    if dcf_base is not None and market_cap is not None:
        reverse_growth = _reverse_dcf(dcf_base, market_cap, terminal_growth, wacc, years,
                                       stage1_years=stage1_years, fade_years=fade_years)

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
        sensitivity = _sensitivity_table(dcf_base, terminal_growth, years, wacc, near_term_growth,
                                          stage1_years=stage1_years, fade_years=fade_years)

    # --- 8b. EPV (Greenwald) — piso de valor sin crecimiento (ADR-029) ---
    net_debt = (total_debt or 0.0) - (cash or 0.0)
    ebit_series = financials.operating_income if financials is not None else []
    epv_ent_mv, epv_ps_mv, epv_upside_pct, epv_source = _compute_epv(
        ebit_series=ebit_series,
        net_income_latest=net_income,
        wacc=wacc,
        net_debt=net_debt,
        shares=shares,
        market_cap=market_cap,
    )

    # --- 8c. WACC advisory (ADR-029) ---
    suggested_wacc, wacc_breakdown = _compute_suggested_wacc(market_cap=market_cap)

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

    # --- 9b. Convergence table (ADR-030) ---
    # Peer EV/EBIT median para la clasificación relativa
    peer_ev_ebit_values = [
        pv.ev_ebit for pv in peer_valuations_raw
        if pv.ev_ebit is not None and not pv.no_market_data and pv.ev_ebit > 0
    ]
    peer_ev_ebit_median: Optional[float] = None
    if peer_ev_ebit_values:
        sv = sorted(peer_ev_ebit_values)
        n = len(sv)
        peer_ev_ebit_median = sv[n // 2] if n % 2 == 1 else (sv[n // 2 - 1] + sv[n // 2]) / 2

    # El historical_cagr para la comparación reverse vs historical usa near_term_growth
    # excepto cuando el usuario lo ha sobreescrito (en ese caso el CAGR histórico viene de revenue_cagr)
    historical_cagr_for_conv = (
        near_term_growth if near_term_growth_source != "user_override" else revenue_cagr
    )
    methods_summary = _build_methods_summary(
        dcf_per_share=dcf_per_share,
        dcf_upside_pct=dcf_upside,
        epv_per_share=epv_ps_mv,
        epv_upside_pct=epv_upside_pct,
        reverse_growth=reverse_growth,
        historical_cagr=historical_cagr_for_conv,
        ev_ebit=ev_ebit_mv,
        peer_ev_ebit_median=peer_ev_ebit_median,
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
        near_term_growth_used_pct=near_term_growth,
        near_term_growth_source=near_term_growth_source,
        stage1_years_used=stage1_years,
        fade_years_used=fade_years,
        epv_enterprise=epv_ent_mv,
        epv_per_share=epv_ps_mv,
        epv_upside_pct=epv_upside_pct,
        epv_source=epv_source,
        suggested_wacc_pct=suggested_wacc,
        suggested_wacc_breakdown=wacc_breakdown,
        valuation_methods_summary=methods_summary,
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
