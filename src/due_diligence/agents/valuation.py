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

from datetime import date
from typing import Optional

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import FcffBreakdown, ValuationInput, ValuationReport, PeerValuation
from due_diligence.utils.formatting import fmt_usd
from due_diligence.utils.periods import cagr_by_fiscal_years
# Aritmética enterprise pura (ADR-033): este módulo orquesta, valuation_math calcula.
from due_diligence.agents.valuation_math import (
    BETA_DEFAULT,
    ERP_PCT,
    MARGINAL_TAX_RATE,
    RF_PCT,
    ValuationInputError,
    compute_wacc,
    dcf_components,
    describe_wacc,
    effective_tax_rate_multi,
    equity_bridge,
    estimate_pre_tax_cost_of_debt,
    fcff_breakdown,
    net_claims,
    per_share,
    resolve_share_count,
    validate_dcf_rates,
)

log = structlog.get_logger()

# Caps para near-term growth — evitan proyecciones absurdas si el FCF histórico
# es ruidoso (p.ej. un año con FCF anormalmente bajo infla el CAGR calculado).
_MAX_NEAR_TERM_GROWTH = 0.30   # 30% máximo
_MIN_NEAR_TERM_GROWTH = -0.15  # -15% mínimo


def _fcf_cagr_with_window(fcf_series: list[MetricValue]) -> tuple[Optional[float], int]:
    """
    Calcula el CAGR del FCF usando la ventana válida más larga disponible (EC-60).

    Problema del enfoque full-span anterior (_fcf_cagr): usaba siempre el punto más
    antiguo como denominador. Si ese año tenía FCF negativo (empresa en turnaround o
    early stage), devolvía None aunque los años recientes fueran perfectamente positivos.
    Ejemplo PATH: FCF negativo FY2022-2023, positivo FY2024-2026 → caída al revenue
    CAGR fallback (15%) cuando el FCF real crecía al ~9.8%.

    Algoritmo: escanea desde el extremo más antiguo hacia el más reciente buscando
    el primer punto con FCF positivo. Calcula el CAGR desde ese punto.
    Ejemplo: [352M, 310M, 292M, -50M, -100M] → i=2 (FY2024=292M>0) → CAGR 2y ≈ 9.8%.

    Returns:
        (cagr, n_periods_used) — CAGR como fracción y períodos reales usados.
        (None, 0) si no existe ninguna ventana válida (newest negativo, o solo 1 punto).
    """
    # EC-83: distancia en AÑOS FISCALES (no índices) y ventana máxima de 5 años.
    # max_years=5 es obligatorio: con periods=10 (EC-79) la serie tiene hasta 10 años y
    # sin tope el growth de stage 1 pasaría a un CAGR de 9 años en todos los tickers.
    r = cagr_by_fiscal_years(fcf_series, max_years=5, scan_for_positive_start=True)
    return r if r else (None, 0)


def _fcf_cagr(fcf_series: list[MetricValue]) -> Optional[float]:
    """
    Calcula el CAGR del FCF desde la serie histórica.

    Wrapper de compatibilidad retroactiva sobre _fcf_cagr_with_window.
    Devuelve solo el CAGR sin el número de períodos utilizados.
    """
    cagr, _ = _fcf_cagr_with_window(fcf_series)
    return cagr


def _three_stage_dcf(
    base_fcf: float,
    g1: float,
    terminal_growth: float,
    wacc: float,
    stage1_years: int,
    fade_years: int = 0,
    mid_year: bool = False,
) -> float:
    """
    DCF en tres etapas — Damodaran fade method (ADR-029), delegado a valuation_math (ADR-033).

    Stage 1 (años 1..stage1_years): crecimiento constante a g1.
    Stage 2 (años stage1_years+1..stage1_years+fade_years): fade lineal g1 → terminal_growth.
    Stage 3: perpetuidad a terminal_growth (Gordon Growth Model).

    fade_years=0 degrada al 2-stage clásico (compatibilidad retroactiva).
    El fade reconoce que mantener g1 durante una década completa es menos probable
    que normalizar hacia el crecimiento de la economía tras los primeros años de ventaja.

    Returns:
        **Enterprise value** (USD): descuenta FCFF al WACC. Para llegar a equity hace falta
        el bridge (valuation_math.equity_bridge); antes de ADR-033 se trataba como equity.

    Raises:
        ValuationInputError: si WACC − g < 1pp. Antes se recortaba g a WACC − 0,5pp en
        silencio, lo que producía TVs de ~200× el FCF (EC-76). Ahora falla loud.
    """
    return dcf_components(
        base_fcf, g1, terminal_growth, wacc, stage1_years, fade_years, mid_year=mid_year,
    ).enterprise_value


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
    mid_year: bool = False,
    nopat_base: Optional[float] = None,
) -> Optional[float]:
    """
    Búsqueda binaria del g1 (stage 1 growth) que iguala el DCF al target_value.

    target_value = **market EV** (equity de mercado + net claims, EC-74), expresado a la fecha
    de cierre fiscal (el llamador deshace la capitalización). Antes se buscaba el market cap con
    un modelo que produce EV: con deuda neta el growth implícito salía sesgado.
    Usa EXACTAMENTE la misma estructura que el forward DCF (3-stage, mid-year y valor terminal
    con reinversión si se pasa nopat_base) para que ambos sean comparables.

    Si stage1_years/fade_years son None, usa years como stage1 y fade=0
    (comportamiento idéntico al 2-stage original).

    Returns:
        Growth rate implícito (fracción, ej 0.12 = 12%) o None si no calculable: FCF o target ≤ 0
        (net-net), o solución pegada a los bordes de búsqueda [-50 %, +200 %] (< 1e-3): un g en
        el borde no es una respuesta, es "fuera de rango".
    """
    if base_fcf <= 0 or target_value <= 0:
        return None

    s1 = stage1_years if stage1_years is not None else years
    fd = fade_years if fade_years is not None else 0

    def _ev(g: float) -> float:
        return dcf_components(base_fcf, g, terminal_growth, wacc, s1, fd,
                              mid_year=mid_year, nopat_base=nopat_base).enterprise_value

    lo_bound, hi_bound = -0.50, 2.00  # rango: -50% a +200% growth anual
    lo, hi = lo_bound, hi_bound
    for _ in range(60):   # 60 iteraciones → precisión ~4 decimal places
        mid = (lo + hi) / 2
        if _ev(mid) < target_value:
            lo = mid
        else:
            hi = mid

    result = (lo + hi) / 2
    if result - lo_bound < 1e-3 or hi_bound - result < 1e-3:
        return None
    return round(result, 4)


def _ten_year_equivalent_cagr(
    g1: float, terminal_growth: float, stage1_years: int, fade_years: int,
) -> Optional[float]:
    """CAGR media equivalente del camino stage1 + fade: (Π(1+g_t))^(1/N) − 1 (revisión M2).

    Un g1 implícito del 8 % con 5 + 5 años de fade a 2,5 % no es comparable con una CAGR
    histórica a 5 años: el equivalente medio es ≈ 6,35 %. La clasificación usa este número.
    """
    n = stage1_years + fade_years
    if n <= 0:
        return None
    path = [g1] * stage1_years + [
        g1 - (g1 - terminal_growth) * t / fade_years for t in range(1, fade_years + 1)]
    growth = 1.0
    for g_t in path:
        growth *= 1 + g_t
    if growth <= 0:
        return None
    return round(growth ** (1 / n) - 1, 4)


def _normalized_fcf_proxy(
    ebit: float, net_income: Optional[float], tax_rate: Optional[float] = None,
) -> Optional[float]:
    """
    Proxy de cash generation normalizado cuando el FCF reportado es negativo (EC-13).

    Cuándo se usa: empresas en trough cíclico (MOS FY2025: FCF -$535M pero EBIT $822M)
    o en heavy reinvestment (growth-stage). Sin este fallback, el DCF se salta y el
    sistema devuelve "N/A — FCF insufficient", lo que pierde toda valoración para una
    clase entera del universo value (cyclicals descontados, compounders en inversión).

    Cálculo: NOPAT = EBIT × (1 − t).
        - t = `tax_rate` si el llamador pasa la tasa canónica (effective_tax_rate_multi).
        - Si no: MARGINAL_TAX_RATE (25 %). La antigua 1 − NI/EBIT metía los intereses
          (y la NCI) en el impuesto: EBIT 300, int 40, NI 195 → 35 % en vez de 25 % (EC-77).
        - `net_income` ya no se usa; se conserva en la firma por compatibilidad de llamadas.

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
    t = tax_rate if tax_rate is not None else MARGINAL_TAX_RATE
    return ebit * (1 - t)


def _fcf_quality_flag(
    fcf_latest: float,
    revenue_latest: float,
    gross_profit_latest: Optional[float],
) -> Optional[str]:
    """
    Detecta FCF posiblemente inflado por advance payments o timing de proyectos (EC-65).

    Patrón SYM: FCF margin 36.7% con gross margin ~20% es imposible desde operaciones
    normalizadas. La diferencia proviene de cobros anticipados de clientes (advance
    payments) que entran como caja pero no como revenue hasta commissioning. Este FCF
    es "FCF de ciclo de proyecto" — no FCF libre recurrente.

    Lógica de detección:
        1. Si FCF/Revenue > 25% Y gross_profit disponible:
             flag si FCF margin > gross margin × 1.3
             (FCF no puede superar sistemáticamente el gross profit sin cobros anticipados)
        2. Si FCF/Revenue > 30% sin gross_profit:
             flag genérico (FCF margin anómalamente alto sin contexto de margen)

    No modifica el DCF — solo emite una advertencia textual para el analista.

    Returns:
        Mensaje de advertencia, o None si no hay anomalía.
    """
    if revenue_latest <= 0:
        return None
    fcf_margin = fcf_latest / revenue_latest
    if fcf_margin < 0.25:
        return None
    if gross_profit_latest is not None and gross_profit_latest > 0:
        gross_margin = gross_profit_latest / revenue_latest
        if fcf_margin > gross_margin * 1.3:
            return (
                f"⚠️ FCF quality: FCF margin ({fcf_margin:.0%}) exceeds gross margin "
                f"({gross_margin:.0%}) × 1.3×. FCF may be inflated by advance payments "
                f"or favorable project timing — not necessarily recurrent free cash flow. "
                f"DCF base may overstate normalized earnings power."
            )
    elif fcf_margin > 0.30:
        return (
            f"⚠️ FCF quality: FCF margin ({fcf_margin:.0%}) is unusually high (>30%). "
            f"May reflect advance payments or deferred revenue. "
            f"DCF base may overstate normalized earnings power."
        )
    return None



# --- Constantes WACC advisory (ADR-029) ---
# Un hecho = un lugar: los valores viven en valuation_math; aquí solo alias históricos
# para no romper imports existentes (cli, tests).
_RF_PCT: float = RF_PCT              # US 10y Treasury, as of 2026-05
_ERP_PCT: float = ERP_PCT            # Damodaran historical ERP (US)
_BETA_DEFAULT: float = BETA_DEFAULT  # simplificación: β=1, sin β específica de empresa


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

    Los años con EBIT negativo SE INCLUYEN (revisión adversarial): descartarlos sesgaba al
    alza el EPV de las cíclicas (un trough es parte del ciclo, no un outlier a ignorar).
    Si la mediana resultante es ≤ 0 no hay poder de beneficio normalizado → (None, label).

    Returns:
        (median_ebit_usd, source_label) donde source: "ebit_median_5y",
        "ebit_median_Ny", "ebit_latest", o (None, "unavailable").
    """
    if not ebit_series:
        return None, "unavailable"
    values = [mv.value for mv in ebit_series if mv.value is not None]
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
    if med <= 0:
        # Mediana ≤ 0: EPV = NOPAT/WACC sería negativo → None; el consumidor muestra "n/a"
        # en lugar de un EPV sin sentido económico.
        return None, label
    return med, label


def _compute_epv(
    ebit_series: list,  # list[MetricValue]
    wacc: float,
    net_claims: float,
    shares: Optional[float],
    price_current: Optional[float] = None,  # EC-63: upside SIEMPRE por acción frente al precio
    mid_year: bool = False,
    *,
    capitalization: float = 1.0,
    tax_rate: Optional[float] = None,
) -> tuple:  # (Optional[MetricValue], Optional[MetricValue], Optional[float], Optional[str], Optional[float])
    """
    Earnings Power Value — Greenwald (ADR-029), en el MISMO plano que el DCF (ADR-033).

    EPV_enterprise = NOPAT_normalizado / WACC   (× (1+WACC)^0.5 si mid_year; × capitalización
                                                 desde el cierre fiscal, igual que el DCF)
    EPV_equity     = EPV_enterprise − net_claims (el mismo bridge que el DCF: deuda, NCI,
                                                 preferentes, caja, STI, LTI)
    EPV_per_share  = EPV_equity / shares

    NOPAT normalizado = mediana(EBIT, incluidos años negativos) × (1 − MARGINAL_TAX_RATE).
    growth_premium = 1 − EPV/sh ÷ precio (Greenwald): fracción del precio que paga por
    crecimiento. Positivo = el mercado paga crecimiento (coherente solo con moat); negativo =
    el precio está por debajo del valor sin crecimiento.

    Sin precio → upside None (se elimina el fallback EV vs market cap, EC-73).

    Returns:
        (epv_enterprise_mv, epv_per_share_mv, epv_upside_pct, source_label, growth_premium)
        Todos pueden ser None si no hay datos suficientes.
    """
    ebit_med, source = _ebit_median(ebit_series)
    if ebit_med is None or wacc <= 0:
        return None, None, None, None, None

    # Tax: el EPV es una perpetuidad → tipo MARGINAL (el efectivo converge a él, Damodaran).
    # La antigua 1 − NI/EBIT metía los intereses en el impuesto (EC-77).
    t = tax_rate if tax_rate is not None else MARGINAL_TAX_RATE
    nopat = ebit_med * (1 - t)
    epv_enterprise = nopat / wacc * ((1 + wacc) ** 0.5 if mid_year else 1.0) * capitalization

    period = ebit_series[0].period if ebit_series else "N/A"
    epv_ent_mv = MetricValue(
        value=round(epv_enterprise),
        unit="USD",
        period=period,
        source=f"EPV ({source})",
    )

    epv_ps_mv: Optional[MetricValue] = None
    if shares is not None and shares > 0:
        epv_ps_mv = MetricValue(
            value=round(per_share(epv_enterprise - net_claims, shares), 2),
            unit="USD/share",
            period=period,
            source=f"EPV ({source}, bridge)",
        )

    epv_upside = _upside_pct(epv_ps_mv.value if epv_ps_mv else None, price_current)
    growth_premium: Optional[float] = None
    if epv_ps_mv is not None and price_current is not None and price_current > 0:
        growth_premium = round(1 - epv_ps_mv.value / price_current, 6)

    return epv_ent_mv, epv_ps_mv, epv_upside, source, growth_premium


def _compute_suggested_wacc(
    market_cap: Optional[float],
    rf_pct: float = _RF_PCT,
    erp_pct: float = _ERP_PCT,
    beta: float = _BETA_DEFAULT,
    size_premium: bool = False,
) -> tuple[float, str]:
    """
    Devuelve el **coste de equity** CAPM (nombre histórico: antes se presentaba como "WACC").

    Fórmula: Ke = Rf + β × ERP (+ size_premium solo si `size_premium=True`)
    Con β=1 (simplificación — sin cálculo de beta específico por empresa).
    El WACC real (con deuda) lo calcula `suggest_wacc` (ADR-033).

    Prima de tamaño OFF por defecto (revisión adversarial): Damodaran la rechaza y con β por
    industria (H3-5) se contaría dos veces. Se sigue mostrando en el desglose para que sea visible.

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

    applied_prem = size_prem if size_premium else 0.0
    suggested = rf_pct + beta * erp_pct + applied_prem
    breakdown = (
        f"Rf={rf_pct}% + β={beta}×ERP={erp_pct}% + size_premium={size_prem}% ({size_label})"
        + ("" if size_premium else " — OFF, not applied")
    )
    return round(suggested, 1), breakdown


def suggest_wacc(financials, market_cap: Optional[float], *, size_premium: bool = False):
    """
    WACC real sugerido (ADR-033) → Optional[WaccBreakdown].

    Ke CAPM (ADR-029, prima de tamaño OFF por defecto) + Kd por rating sintético (cobertura
    EBIT/intereses del último ejercicio) + escudo fiscal al tipo MARGINAL + pesos a mercado
    (E = market cap; D = deuda contable del último año, proxy del valor de mercado).
    None sin market cap: no hay pesos de mercado y no se inventan (el llamador cae a 9,0 %).
    """
    if market_cap is None or market_cap <= 0 or financials is None:
        return None
    ke_pct, ke_src = _compute_suggested_wacc(market_cap, size_premium=size_premium)  # histórico: devuelve Ke
    ebit = financials.operating_income[0].value if financials.operating_income else None
    interest = financials.interest_expense[0].value if financials.interest_expense else None
    # Serie por año (T9) primero; el punto del balance como fallback; sin deuda → 0 (peso 0).
    debt_latest = (financials.total_debt_series[0].value if financials.total_debt_series
                   else (financials.total_debt.value if financials.total_debt else 0.0))
    kd, kd_src = estimate_pre_tax_cost_of_debt(ebit=ebit, interest_expense=interest)
    return compute_wacc(ke_pct / 100, kd, MARGINAL_TAX_RATE, market_cap, max(debt_latest, 0.0),
                        cost_of_equity_source=f"CAPM: {ke_src}", cost_of_debt_source=kd_src)


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
    Clasifica el upside del EPV por acción frente al precio (ADR-033 §8: banda ±10 % simétrica).

    >10%: cheap (el precio está por debajo del valor sin crecimiento).
    [-10%, 10%]: fair.
    <-10%: expensive (el precio paga crecimiento; leer junto a epv_growth_premium y el moat).
    Antes <0 % ya era "expensive": asimétrico frente al DCF (±20 %) y penalizaba cualquier
    prima de crecimiento por pequeña que fuera.
    """
    if upside_pct is None:
        return "n/a"
    if upside_pct > 10:
        return "cheap"
    if upside_pct < -10:
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
    # EV/EBIT no tiene significado económico cuando el EBIT es negativo o cero (EC-62).
    # Un ratio negativo vs peers positivo produce "cheap" incorrecto.
    if subject_ev_ebit is None or subject_ev_ebit <= 0:
        return "n/a"
    if peer_ev_ebit_median is None or peer_ev_ebit_median <= 0:
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
    reverse_10y_equivalent: Optional[float] = None,
) -> list[dict]:
    """
    Tabla de 4 métodos de valoración con clasificación uniforme (ADR-030, umbrales ADR-033 §8).

    Cada fila: method, per_share, display_upside, classification.
    Consumida por Value Expert (_format_context) y Archiver.

    Reverse DCF (revisión M2): si se pasa `reverse_10y_equivalent`, la clasificación compara la
    CAGR equivalente a 10 años del camino implícito (stage1 + fade) con la CAGR histórica de
    ingresos — un g1 del 8 % con fade no es comparable con una CAGR plana de 5 años.

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
                if reverse_10y_equivalent is None else
                f"implied g1 {_fmt_pct(reverse_growth)} (10y-equiv {_fmt_pct(reverse_10y_equivalent)}) "
                f"vs historical {_fmt_pct(historical_cagr)} revenue CAGR"
            ),
            "classification": _classify_reverse_vs_historical(
                reverse_10y_equivalent if reverse_10y_equivalent is not None else reverse_growth,
                historical_cagr,
            ),
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
        # _fcf_cagr_with_window devuelve la ventana real usada, no len-1 fijo (EC-60).
        raw_cagr, n_periods = _fcf_cagr_with_window(financials.free_cash_flow)

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
    *,
    net_claims: float,
    share_count: float,
    mid_year: bool = False,
    nopat_base: Optional[float] = None,
    days_since_fye: int = 0,
) -> list[dict]:
    """
    Tabla 3×3: eje X = WACC ± 2%, eje Y = g1 (stage 1 growth) ± 5%. Celdas en **USD/share**.

    EC-64: delta ampliado de ±1% a ±2% para cubrir empresas de alto riesgo donde el
    WACC apropiado puede superar en 4-5pp el advisory (e.g. SYM: advisory 9.7%,
    WACC razonable 14-17%). Con ±1% el analista veía solo 13-15%; con ±2% ve 12-16%.

    Cada celda recorre el MISMO camino que el titular del DCF (ADR-033): dcf_components (con
    reinversión terminal si nopat_base y mid-year si procede) → EV × (1+w)^(días/365) →
    − net_claims → / share_count. Así la celda central reproduce el valor por acción del informe.
    Antes era el "equity value" total en USD B sin bridge: no comparable con el precio.
    Celda con WACC − g < 1pp → None ("n/m"). El fade se mantiene fijo; solo g1 y WACC varían.

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
        for w_delta in (-0.02, 0.0, 0.02):
            w = wacc_center + w_delta
            key = f"wacc_{round(w*100,1)}pct"
            try:
                ev = dcf_components(base_fcf, g, terminal_growth, w, s1, fd,
                                    mid_year=mid_year, nopat_base=nopat_base).enterprise_value
                ev *= (1 + w) ** (days_since_fye / 365)
                row["values"][key] = round(per_share(ev - net_claims, share_count), 2)
            except ValuationInputError:
                # WACC − 2pp puede caer por debajo de g + 1pp (p.ej. WACC 4.5 %, g 2.5 %):
                # celda None ("n/m") en lugar de tumbar el nodo entero (revisión adversarial, T10).
                row["values"][key] = None
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


# ---------------------------------------------------------------------------
# Helpers del plano enterprise (T12a, ADR-033)
# ---------------------------------------------------------------------------

def _latest(series) -> Optional[float]:
    """Valor más reciente de una serie MetricValue (index 0 = latest), o None."""
    return series[0].value if series else None


def _mv_val(mv) -> Optional[float]:
    """Valor de un MetricValue OPCIONAL (no de una lista: para listas usar _latest)."""
    return mv.value if mv is not None else None


def _upside_pct(per_share_value: Optional[float], price: Optional[float]) -> Optional[float]:
    """(per_share − price)/price en %, o None si no hay precio (nunca EV vs market cap, EC-73)."""
    if per_share_value is None or not price or price <= 0:
        return None
    return round((per_share_value - price) / price * 100, 1)


def _canonical_tax_rate(financials) -> float:
    """Tipo efectivo canónico (effective_tax_rate_multi) con las series alineadas por PERIODO.

    effective_tax_rate_multi empareja por posición; si income_tax y pretax_income no tienen
    los mismos años (huecos en XBRL), emparejar por índice mezclaría ejercicios. Se alinean
    aquí por etiqueta FY y se pasan ya emparejados. Sin datos → 25 % (default canónico).
    """
    if financials is None:
        return effective_tax_rate_multi([], [])
    pretax_by = {m.period: m.value for m in financials.pretax_income}
    pairs = [(m.value, pretax_by[m.period]) for m in financials.income_tax if m.period in pretax_by]
    return effective_tax_rate_multi([t for t, _ in pairs], [p for _, p in pairs])


def _fcff_for_year(financials, i: int, tax_rate: float) -> Optional[FcffBreakdown]:
    """FCFF del ejercicio en posición i de operating_cash_flow, con cada input del MISMO periodo.

    Un input ausente para ese año cuenta 0.0 (y se lista en missing_inputs por _build_fcff):
    emparejar por etiqueta FY evita mezclar el capex de un año con el OCF de otro (EC-82).
    """
    ocf = financials.operating_cash_flow
    if i >= len(ocf):
        return None
    period = ocf[i].period

    def _at(series) -> float:   # valor de la serie en el MISMO periodo, 0.0 si falta
        return next((m.value for m in series if m.period == period), 0.0)

    return fcff_breakdown(
        ocf=ocf[i].value, capex=_at(financials.capex),
        interest_expense=_at(financials.interest_expense), tax_rate=tax_rate,
        sbc=_at(financials.sbc), interest_income=_at(financials.interest_income),
        finance_lease_principal=_at(financials.finance_lease_principal_payments),
    )


def _build_fcff(financials, tax_rate: float) -> tuple[Optional[FcffBreakdown], Optional[float], list[str], str]:
    """
    Base FCFF del DCF (revisión adversarial A2).

    Si hay ≥3 ejercicios con ingresos: mediana del margen FCFF/ingresos de hasta 3 años × ingresos
    del último (like-with-like con el EPV, que usa mediana de EBIT; un año con capex o capital
    circulante atípico no fija toda la valoración). Si no: FCFF del último ejercicio.

    Returns:
        (breakdown del ÚLTIMO año — componentes coherentes entre sí —, base usada en el DCF,
         inputs ausentes, etiqueta de fuente).
    """
    if financials is None or not financials.operating_cash_flow:
        return None, None, ["operating_cash_flow"], "unavailable"
    latest = _fcff_for_year(financials, 0, tax_rate)
    missing = [n for n, s in (("capex", financials.capex), ("interest_expense", financials.interest_expense),
                              ("sbc", financials.sbc), ("interest_income", financials.interest_income))
               if not s]
    latest = latest.model_copy(update={"missing_inputs": missing})
    rev_by = {m.period: m.value for m in financials.revenue}
    margins = []
    for i in range(min(3, len(financials.operating_cash_flow))):
        bd = _fcff_for_year(financials, i, tax_rate)
        rev = rev_by.get(financials.operating_cash_flow[i].period)
        if bd is not None and rev:
            margins.append(bd.fcff / rev)
    if len(margins) >= 3 and financials.revenue:
        median_margin = sorted(margins)[1]
        base = median_margin * financials.revenue[0].value
        return latest, base, missing, f"median FCFF margin (3y) × latest revenue = {median_margin:.1%}"
    return latest, latest.fcff, missing, "latest FY FCFF"


def valuation_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: calcula valor intrínseco usando aritmética pura (ADR-033).

    Lee FinancialsReport y MarketReport del estado + assumptions del usuario
    (checkpoint2_user_input). Si algún dato no está disponible, produce un
    ValuationReport parcial en lugar de fallar.

    Fail loud SIN tumbar el grafo: un input inválido (WACC − g < 1pp, signo de capex
    negativo...) lanza ValuationInputError en la aritmética; aquí se captura y se devuelve
    ValuationReport(stub=True, error=...) para que archiver y síntesis lo traten como
    DATA GAP visible (un caller programático o la web no pasan por la validación de la CLI).

    Returns:
        {"valuation": ValuationReport(...)} (+ "peer_valuations" con mf_rank si hay peers)
    """
    ticker = state["ticker"]
    try:
        return _run_valuation(state)
    except ValuationInputError as e:
        log.error("valuation_input_error", ticker=ticker, error=str(e))
        return {"valuation": ValuationReport(ticker=ticker, stub=True, error=f"ValuationInputError: {e}")}


def _run_valuation(state: AnalysisState) -> dict:
    """Cuerpo de valuation_agent. Puede lanzar ValuationInputError (lo captura el nodo)."""
    # Import diferido: checkpoint importa valuation (suggest_wacc) dentro de una función;
    # importarlo aquí a nivel de módulo crearía un ciclo checkpoint ↔ valuation.
    from due_diligence.agents.checkpoint import CHECKPOINT2_DEFAULTS

    ticker = state["ticker"]
    financials = state.get("financials")   # type: ignore[typeddict-item]
    market = state.get("market")           # type: ignore[typeddict-item]
    user_raw: dict = state.get("checkpoint2_user_input") or {}  # type: ignore[typeddict-item]

    log.info("valuation_agent_start", ticker=ticker)

    # --- 1. Market cap y precio (antes que las assumptions: el WACC por defecto los necesita) ---
    market_cap: Optional[float] = None
    price_current_mv: Optional[MetricValue] = None
    as_of_date: str = "N/A"
    if market is not None and market.market_cap is not None:
        market_cap = market.market_cap.value
        if market.market_cap.period:
            as_of_date = market.market_cap.period
    if market is not None and market.price_current is not None:
        price_current_mv = market.price_current
    price: Optional[float] = (
        price_current_mv.value if price_current_mv is not None and price_current_mv.value > 0 else None
    )

    # --- 1b. Assumptions: checkpoint2_user_input + defaults únicos (CHECKPOINT2_DEFAULTS) ---
    # WACC por defecto = WACC real sugerido (ADR-033); 9,0 solo sin market cap.
    wacc_suggestion = suggest_wacc(financials, market_cap)
    if user_raw.get("wacc_pct") is not None:
        wacc_pct = float(user_raw["wacc_pct"])
    elif wacc_suggestion is not None:
        wacc_pct = round(wacc_suggestion.wacc * 100, 1)
    else:
        wacc_pct = float(CHECKPOINT2_DEFAULTS["wacc_pct"])
    raw_date = user_raw.get("valuation_date")
    valuation_date = (raw_date if isinstance(raw_date, date)
                      else date.fromisoformat(str(raw_date)) if raw_date else date.today())
    assumptions = ValuationInput(
        checkpoint_id="checkpoint2",
        wacc_pct=wacc_pct,
        terminal_growth_pct=float(user_raw.get("terminal_growth_pct", CHECKPOINT2_DEFAULTS["terminal_growth_pct"])),
        projection_years=int(user_raw.get("projection_years", CHECKPOINT2_DEFAULTS["projection_years"])),
        stage1_years=user_raw.get("stage1_years"),
        fade_years=user_raw.get("fade_years"),
        revenue_growth_yr1_pct=user_raw.get("revenue_growth_yr1_pct"),
        margin_assumption_pct=user_raw.get("margin_assumption_pct"),
        # Cableado explícito (revisión adversarial): antes el flag nunca llegaba al ValuationInput.
        mid_year_convention=bool(user_raw.get("mid_year_convention", CHECKPOINT2_DEFAULTS["mid_year_convention"])),
        valuation_date=valuation_date,
    )
    wacc = assumptions.wacc_pct / 100
    terminal_growth = assumptions.terminal_growth_pct / 100
    years = assumptions.projection_years
    # Fail loud temprano (EC-76): lanza ValuationInputError → valuation_agent lo convierte en error visible.
    validate_dcf_rates(wacc, terminal_growth)

    # 3-stage split: resolved from user input with auto 50/50 fallback (ADR-029)
    stage1_years, fade_years = _resolve_stages(
        years, assumptions.stage1_years, assumptions.fade_years
    )

    # --- 2. Extraer métricas del FinancialsReport ---
    base_fcf: Optional[float] = None       # FCF apalancado (OCF − capex): solo growth y flags legacy
    ebit: Optional[float] = None
    net_income: Optional[float] = None
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    fcf_period: str = "N/A"

    revenue_latest: Optional[float] = None
    gross_profit_latest: Optional[float] = None

    if financials is not None:
        if financials.free_cash_flow:
            base_fcf = financials.free_cash_flow[0].value
            fcf_period = financials.free_cash_flow[0].period
        if financials.operating_cash_flow:
            fcf_period = financials.operating_cash_flow[0].period   # periodo del FCFF
        if financials.operating_income:
            ebit = financials.operating_income[0].value
        if financials.net_income:
            net_income = financials.net_income[0].value
        if financials.total_debt is not None:
            total_debt = financials.total_debt.value
        if financials.cash_and_equivalents is not None:
            # SOLO caja desde T6 (EC-81); STI y LTI se restan UNA vez en el bridge de abajo.
            cash = financials.cash_and_equivalents.value
        if financials.revenue:
            revenue_latest = financials.revenue[0].value
        if financials.gross_profit:
            gross_profit_latest = financials.gross_profit[0].value

    # --- 2b. Plano enterprise (ADR-033): tipo, FCFF, acciones, derechos netos, market EV ---
    note_parts: list[str] = []
    tax_rate = _canonical_tax_rate(financials)
    fcff_bd, fcff_base_value, fcff_missing, fcff_source = _build_fcff(financials, tax_rate)
    share_count, share_source = resolve_share_count(
        _mv_val(financials.shares_outstanding) if financials is not None else None,
        _latest(financials.diluted_shares) if financials is not None else None,
        _mv_val(financials.basic_shares_weighted) if financials is not None else None,
        market_cap, price,
    )
    lt_inv: Optional[float] = None
    equity_method: Optional[float] = None
    if financials is not None:
        lt_inv = _mv_val(financials.long_term_investments)
        equity_method = _mv_val(financials.equity_method_investments)
    # Participaciones por el método de la participación: su resultado NO está en EBIT/FCFF, así que
    # se suman como activo no operativo junto a las LTI (revisión adversarial), con aviso visible.
    if equity_method:
        lt_inv = (lt_inv or 0.0) + equity_method
        note_parts.append(
            f"ℹ️ Equity-method investments ({fmt_usd(equity_method)}) added to the bridge as a "
            f"non-operating asset — verify they do not generate operating income."
        )
    claims_kwargs = dict(
        total_debt=total_debt or 0.0, cash=cash or 0.0,
        sti=_mv_val(financials.short_term_investments) if financials is not None else None,
        lt_investments=lt_inv,
        minority_interest=_mv_val(financials.minority_interest) if financials is not None else None,
        preferred=_mv_val(financials.preferred_stock) if financials is not None else None,
    )
    claims = net_claims(**claims_kwargs)
    # Equity de mercado coherente con el per-share (EC-63): precio × mismas acciones que el DCF.
    equity_mkt: Optional[float] = price * share_count if (price and share_count) else market_cap
    market_ev: Optional[float] = equity_mkt + claims if equity_mkt is not None else None
    if market_ev is not None and market_ev <= 0:
        note_parts.append("ℹ️ Net-net: net cash exceeds market equity (market EV ≤ 0); EV multiples n/m.")

    # Capitalización desde el cierre fiscal (revisión M5): los flujos se descuentan al cierre,
    # pero el precio es de hoy → EV × (1+WACC)^(días/365). Mismo factor para DCF, EPV y reverse.
    days_since_fye = 0
    if financials is not None and assumptions.valuation_date is not None:
        days_since_fye = max((assumptions.valuation_date - financials.as_of_date).days, 0)
    capitalization = (1 + wacc) ** (days_since_fye / 365)

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
    raw_cagr, _ = (
        _fcf_cagr_with_window(financials.free_cash_flow)
        if financials is not None and financials.free_cash_flow else (None, 0)
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
    # La línea de growth va primero: es el parámetro más sensible y el lector debe verlo antes.
    note_parts.insert(0, growth_context)

    if growth_was_capped and assumptions.revenue_growth_yr1_pct is None:
        note_parts.append(
            f"⚠️ DCF growth capped at {_MAX_NEAR_TERM_GROWTH:.0%} "
            f"(raw FCF CAGR was {raw_cagr:.0%}). "
            f"The base FCF may be at a cyclical peak — forward DCF likely overstates intrinsic value."
        )
        log.warning("valuation_dcf_growth_capped", ticker=ticker, raw_cagr=f"{raw_cagr:.0%}")

    # EC-65: FCF quality flag — detecta FCF inflado por advance payments o project timing.
    # No modifica el DCF; solo añade advertencia cuando FCF margin >> gross margin.
    fcf_quality_warning: Optional[str] = None
    if base_fcf is not None and base_fcf > 0 and revenue_latest is not None and revenue_latest > 0:
        fcf_quality_warning = _fcf_quality_flag(base_fcf, revenue_latest, gross_profit_latest)
        if fcf_quality_warning:
            note_parts.append(fcf_quality_warning)
            log.warning("valuation_fcf_quality_flag", ticker=ticker,
                        fcf_margin=f"{base_fcf/revenue_latest:.0%}")

    # --- P5: Cyclical peak base qualifier ---
    # Si el revenue más reciente supera la mediana histórica en >15%, el CAGR está
    # anclado a un año boom. Ejemplo: CGNX FY2022 (pico de automatización post-COVID)
    # → CAGR 5y aparece -0.4%, pero desde trough es +9%. El LLM y el usuario deben
    # saber que las proyecciones parten de una base potencialmente elevada.
    if (
        financials is not None
        and financials.revenue is not None
        and len(financials.revenue) >= 3
        and assumptions.revenue_growth_yr1_pct is None  # no si el usuario ya overrideó
    ):
        # Ventana de 5 años (T7): con periods=10 la mediana de 10 años cambiaría el aviso en silencio.
        rev_values = [r.value for r in financials.revenue[:5] if r.value and r.value > 0]
        if len(rev_values) >= 3:
            rev_median = sorted(rev_values)[len(rev_values) // 2]
            rev_latest = rev_values[0]
            if rev_median > 0 and rev_latest > rev_median * 1.15:
                peak_ratio = rev_latest / rev_median
                note_parts.append(
                    f"⚠️ Cyclical peak base: current revenue is {peak_ratio:.1f}x the "
                    f"historical median of the last {len(rev_values)} years. "
                    f"The CAGR-derived growth rate may be anchored to a boom year — "
                    f"forward DCF could overstate sustainable growth. "
                    f"Consider using a normalized (mid-cycle) revenue base for projections."
                )
                log.info(
                    "valuation_cyclical_peak_base_detected",
                    ticker=ticker,
                    rev_latest=fmt_usd(rev_latest),
                    rev_median=fmt_usd(rev_median),
                    ratio=f"{peak_ratio:.2f}",
                )

    log.info(
        "valuation_agent_assumptions",
        ticker=ticker,
        wacc_pct=assumptions.wacc_pct,
        near_term_growth_pct=round(near_term_growth * 100, 1),
        terminal_growth_pct=assumptions.terminal_growth_pct,
        years=years,
    )

    # --- 5. DCF sobre FCFF; fallback NOPAT (ya unlevered → misma base, EC-13) ---
    # NOPAT del último año con el tipo canónico: ancla del valor terminal con reinversión
    # (Damodaran/Koller) y fallback del DCF cuando el FCFF es negativo (trough / reinversión fuerte).
    nopat_base: Optional[float] = ebit * (1 - tax_rate) if ebit is not None else None
    terminal_nopat = nopat_base if nopat_base is not None and nopat_base > 0 else None
    dcf_base: Optional[float] = None
    dcf_base_source: str = ""  # etiqueta para `MetricValue.source` y reliability note
    if fcff_base_value is not None and fcff_base_value > 0:
        dcf_base, dcf_base_source = fcff_base_value, f"FCFF ({fcff_source})"
    elif terminal_nopat is not None:
        dcf_base, dcf_base_source = terminal_nopat, "NOPAT proxy (EBIT × (1-t))"
    else:
        note_parts.append("⚠️ DCF not computed: no positive FCFF or NOPAT (loss-making or data gap).")
        log.warning("valuation_agent_no_dcf_base", ticker=ticker, fcff=fcff_base_value, ebit=ebit)

    dcf_value: Optional[MetricValue] = None
    dcf_per_share: Optional[MetricValue] = None
    dcf_upside: Optional[float] = None
    ev_dcf_mv: Optional[MetricValue] = None
    bridge = None
    comps = None
    if dcf_base is not None:
        comps = dcf_components(dcf_base, near_term_growth, terminal_growth, wacc, stage1_years, fade_years,
                               mid_year=assumptions.mid_year_convention, nopat_base=terminal_nopat)
        ev = comps.enterprise_value * capitalization
        if days_since_fye > 0:
            note_parts.append(f"ℹ️ EV capitalized {days_since_fye} days from fiscal year-end to valuation date.")
        bridge = equity_bridge(
            ev, **claims_kwargs,
            lease_liabilities=_mv_val(financials.operating_lease_liabilities) if financials is not None else None,
        )
        ev_dcf_mv = MetricValue(value=round(ev), unit="USD", period=fcf_period,
                                source=f"calculated ({dcf_base_source} at WACC)")
        dcf_value = MetricValue(value=round(bridge.equity_value), unit="USD", period=fcf_period,
                                source=f"calculated ({dcf_base_source}, EV − net claims)")
        if share_count:
            ps = per_share(bridge.equity_value, share_count)
            dcf_per_share = MetricValue(value=round(ps, 2), unit="USD/share", period=fcf_period,
                                        source=f"calculated ({dcf_base_source}, bridge)")
            dcf_upside = _upside_pct(dcf_per_share.value, price)
            if dcf_upside is None:
                note_parts.append("⚠️ Upside not computed: price unavailable.")
        else:
            note_parts.append("⚠️ Per-share value not computed: share count unavailable.")
        if bridge.equity_value < 0:
            note_parts.append("⚠️ Equity value negative: net claims exceed DCF enterprise value.")
        if comps.terminal_value_share and comps.terminal_value_share > 0.75:
            note_parts.append(f"⚠️ {comps.terminal_value_share:.0%} of value comes from the terminal value.")

    # Notas obligatorias de inputs (ADR-033): lo ausente se dice, no se maquilla.
    if "sbc" in fcff_missing:
        note_parts.append("⚠️ SBC unavailable: FCFF may be overstated (stock comp not charged).")
    if "interest_expense" in fcff_missing and (total_debt or 0) > 0:
        note_parts.append("⚠️ Interest expense unavailable: after-tax interest not added back (FCFF understated).")
    if "interest_income" in fcff_missing and bridge is not None and bridge.enterprise_value > 0:
        liquid = (cash or 0.0) + (claims_kwargs["sti"] or 0.0)
        if liquid > 0.10 * bridge.enterprise_value:
            note_parts.append("⚠️ Interest income unavailable while cash+STI > 10% of EV: "
                              "possible double count of cash yield (in FCFF and in the bridge).")
    if bridge is not None and bridge.components_missing:
        note_parts.append(f"ℹ️ Bridge components unavailable (treated as 0): {', '.join(bridge.components_missing)}.")
    if share_count and any(k in share_source for k in ("dilution not applied", "approximation", "overstates")):
        note_parts.append(f"ℹ️ Share count: {share_source}.")

    # --- 6. Reverse DCF — objetivo = MARKET EV (EC-74), misma función y base que el directo ---
    # El market EV es de hoy; el modelo produce EV a fecha de cierre → se deshace la
    # capitalización para comparar en la misma fecha. Market EV ≤ 0 (net-net) → None.
    reverse_growth: Optional[float] = None
    if dcf_base is not None and market_ev is not None and market_ev > 0:
        reverse_growth = _reverse_dcf(dcf_base, market_ev / capitalization, terminal_growth, wacc, years,
                                       stage1_years=stage1_years, fade_years=fade_years,
                                       mid_year=assumptions.mid_year_convention, nopat_base=terminal_nopat)

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

    # Nota EC-13: cuando usamos NOPAT proxy en lugar de FCFF, documentarlo explícitamente.
    # El lector debe saber que la valoración es directional, no point estimate.
    if dcf_base_source.startswith("NOPAT") and dcf_base is not None and fcff_base_value is not None:
        note_parts.append(
            f"⚠️ DCF computed on NORMALIZED NOPAT ({fmt_usd(dcf_base)} = EBIT × (1-tax)) "
            f"because FCFF was negative ({fmt_usd(fcff_base_value)} in {fcf_period}). "
            f"This is appropriate for cyclical troughs or heavy reinvestment but ignores "
            f"working capital and maintenance vs growth capex split — treat as directional, "
            f"not point estimate. Reverse DCF implied growth uses same NOPAT base."
        )
        log.info(
            "valuation_dcf_nopat_fallback",
            ticker=ticker,
            fcff=fcff_base_value,
            ebit=ebit,
            nopat_proxy=dcf_base,
        )

    # --- 7. Múltiplos (requieren market cap) ---
    ev_ebit_mv: Optional[MetricValue] = None
    ev_fcf_mv: Optional[MetricValue] = None
    pe_mv: Optional[MetricValue] = None
    earnings_yield: Optional[float] = None

    period_label = as_of_date
    # EV de mercado = equity de mercado (precio × acciones) + net claims: la MISMA definición de
    # derechos que el bridge del DCF/EPV (ADR-033 §5). Market EV ≤ 0 (net-net) → múltiplos "n/m".
    if market_ev is not None and market_ev > 0:
        if ebit is not None:
            ev_ebit_mv = MetricValue(
                value=round(market_ev / ebit, 2),
                unit="x",
                period=period_label,
                source="calculated (market EV / EBIT)",
            )
            earnings_yield = round(ebit / market_ev * 100, 2)  # EBIT/EV × 100

        # EV/FCFF (antes EV/FCF apalancado): numerador y denominador en el mismo plano enterprise.
        if fcff_base_value is not None and fcff_base_value > 0:
            ev_fcf_mv = MetricValue(
                value=round(market_ev / fcff_base_value, 2),
                unit="x",
                period=period_label,
                source="calculated (market EV / FCFF)",
            )

    if equity_mkt is not None and equity_mkt > 0 and net_income is not None and net_income > 0:
        pe_mv = MetricValue(
            value=round(equity_mkt / net_income, 2),
            unit="x",
            period=period_label,
            source="calculated (price × shares / NI)",
        )

    # --- 8. Sensitivity table — usa dcf_base (FCF o NOPAT proxy según fallback EC-13) ---
    # Celdas en USD/share por el mismo camino que el titular (bridge + capitalización + reinversión).
    sensitivity: list[dict] = []
    if dcf_base is not None and dcf_base > 0:
        if share_count:
            sensitivity = _sensitivity_table(
                dcf_base, terminal_growth, years, wacc, near_term_growth,
                stage1_years=stage1_years, fade_years=fade_years,
                net_claims=claims, share_count=share_count,
                mid_year=assumptions.mid_year_convention, nopat_base=terminal_nopat,
                days_since_fye=days_since_fye,
            )
        else:
            note_parts.append("⚠️ Sensitivity table not computed: share count unavailable.")

    # --- 8b. EPV (Greenwald) — piso de valor sin crecimiento (ADR-029) ---
    # Mismo bridge (claims) y misma capitalización a fecha de valoración que el DCF (ADR-033):
    # antes el DCF no restaba deuda y el EPV sí → la convergencia comparaba planos distintos.
    ebit_series = financials.operating_income if financials is not None else []
    epv_ent_mv, epv_ps_mv, epv_upside_pct, epv_source, epv_growth_premium = _compute_epv(
        ebit_series=ebit_series,
        wacc=wacc,
        net_claims=claims,
        shares=share_count,
        price_current=price,
        mid_year=assumptions.mid_year_convention,
        capitalization=capitalization,
    )
    if epv_growth_premium is not None:
        # Lectura de Greenwald: el EPV no es "caro/barato" a secas; dice cuánto paga el precio
        # por crecimiento, que solo es valioso si hay moat (ROIC > WACC sostenido).
        note_parts.append(
            f"ℹ️ EPV growth premium: {epv_growth_premium:.0%} of the price pays for growth."
            if epv_growth_premium >= 0 else
            f"ℹ️ EPV growth premium: price is {-epv_growth_premium / (1 - epv_growth_premium):.0%} "
            f"below the no-growth value (EPV)."
        )

    # --- 8c. WACC sugerido (ADR-033): WACC real con su desglose ---
    suggested_wacc: Optional[float] = round(wacc_suggestion.wacc * 100, 1) if wacc_suggestion else None
    wacc_breakdown_str: Optional[str] = describe_wacc(wacc_suggestion) if wacc_suggestion else None

    # EC-64: si el WACC usado difiere >3pp del sugerido, añadir nota visible al analista.
    # El sugerido usa β=1 sin company-specific risk — para empresas de alto riesgo
    # (concentración de clientes, SEC investigation, restatements) la diferencia puede
    # ser estructural. La sensitivity table (±2%) cubre el rango relevante.
    if suggested_wacc is not None:
        wacc_delta_pp = round(assumptions.wacc_pct - suggested_wacc, 1)
        if abs(wacc_delta_pp) > 3.0:
            direction = "above" if wacc_delta_pp > 0 else "below"
            note_parts.append(
                f"WACC used ({assumptions.wacc_pct}%) is {abs(wacc_delta_pp):.1f}pp {direction} "
                f"suggested real WACC ({suggested_wacc}%). The suggestion uses β=1.0 (no company-specific "
                f"risk). For high-risk profiles (customer concentration >50%, active SEC investigation, "
                f"restatements), consider adding 2-5pp. Sensitivity table covers "
                f"{assumptions.wacc_pct - 2:.1f}%–{assumptions.wacc_pct + 2:.1f}%."
            )

    if note_parts:
        dcf_reliability_note = " ".join(note_parts)

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

    # Histórico de convergencia = CAGR de INGRESOS (ADR-033 §9, EC-75). Antes era
    # near_term_growth, que ya es el input del DCF directo: comparar el reverse contra él era
    # circular (el "histórico" salía del mismo FCF CAGR que alimentaba el modelo).
    historical_cagr_for_conv = revenue_cagr
    # Reverse comparable (revisión M2): CAGR media a 10 años del camino implícito stage1 + fade.
    reverse_10y = (
        _ten_year_equivalent_cagr(reverse_growth, terminal_growth, stage1_years, fade_years)
        if reverse_growth is not None else None
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
        reverse_10y_equivalent=reverse_10y,
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
        reverse_dcf_10y_equivalent=reverse_10y,
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
        epv_growth_premium=epv_growth_premium,
        suggested_wacc_pct=suggested_wacc,
        suggested_wacc_breakdown=wacc_breakdown_str,
        wacc_breakdown=wacc_suggestion,
        valuation_methods_summary=methods_summary,
        fcf_quality_warning=fcf_quality_warning,
        # --- Plano enterprise (ADR-033) ---
        valuation_basis="FCFF discounted at WACC → EV → equity bridge → per share (ADR-033)",
        fcff_base=(MetricValue(value=round(fcff_base_value), unit="USD", period=fcf_period, source=fcff_source)
                   if fcff_base_value is not None else None),
        fcff_breakdown=fcff_bd,
        dcf_base_source=dcf_base_source or None,
        tax_rate_used=tax_rate,
        enterprise_value_dcf=ev_dcf_mv,
        equity_bridge=bridge,
        market_enterprise_value=(MetricValue(value=round(market_ev), unit="USD", period=as_of_date,
                                             source="price × shares + net claims")
                                 if market_ev is not None else None),
        share_count_used=share_count,
        share_count_source=share_source,
        terminal_value_share=comps.terminal_value_share if comps is not None else None,
        mid_year_convention_used=assumptions.mid_year_convention,
        sensitivity_unit="USD/share" if sensitivity else None,
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
