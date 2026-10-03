"""
valuation_math — aritmética pura de valoración a nivel ENTERPRISE (ADR-033).

Por qué existe: DCF, EPV y reverse DCF deben vivir en el mismo plano (FCFF descontado al
WACC → EV → equity bridge → por acción) para que la convergencia de la ADR-030 compare
like-with-like. Sin I/O, sin state, sin LLM: funciones deterministas fáciles de testear a mano.
valuation.py orquesta; este módulo solo calcula. Todos los importes en USD, tasas en fracción.
"""
from __future__ import annotations

from typing import Optional

from due_diligence.schemas.reports import DcfComponents, EquityBridge, FcffBreakdown, WaccBreakdown

# Tipo impositivo: UNA sola definición canónica (revisión adversarial). Vive en tools.xbrl_extract
# porque es una propiedad de los datos (Σtax/Σpretax); aquí solo se re-exporta para los
# consumidores de valoración. NO redefinir: dos fórmulas de impuestos daban 5 % vs 25 % en PRDO.
from due_diligence.tools.xbrl_extract import effective_tax_rate, effective_tax_rate_multi  # noqa: F401

__all__ = [
    "ValuationInputError", "RF_PCT", "ERP_PCT", "BETA_DEFAULT", "MARGINAL_TAX_RATE",
    "effective_tax_rate", "effective_tax_rate_multi", "validate_dcf_rates",
    "fcff_breakdown", "compute_fcff", "net_claims", "equity_bridge", "per_share",
    "resolve_share_count", "dcf_components", "estimate_pre_tax_cost_of_debt", "compute_wacc",
    "describe_wacc",
]


class ValuationInputError(ValueError):
    """Assumption o input de valoración inválido. Fail loud (invariante #5): nunca recortar en silencio."""


# Tasa libre de riesgo, ERP y beta: un hecho = un lugar. valuation.py las re-exporta (ADR-029).
RF_PCT: float = 4.2     # US 10y, as of 2026-05 — actualizar manualmente (rf dinámica = H3)
ERP_PCT: float = 5.0    # Damodaran historical ERP (US)
BETA_DEFAULT: float = 1.0

# Tipo MARGINAL para valor terminal, EPV y escudo fiscal del WACC: el efectivo converge al marginal
# en perpetuidad (Damodaran). 21 % federal + estatal ≈ 25 %.
MARGINAL_TAX_RATE: float = 0.25

_MIN_WACC_MINUS_G = 0.01           # con un spread < 1pp el TV de Gordon supera 100× el FCF
_DEFAULT_CREDIT_SPREAD = 0.02      # fallback de Kd = Rf + 2pp (≈ BBB) cuando no hay cobertura calculable


def validate_dcf_rates(wacc: float, terminal_growth: float) -> None:
    """Lanza ValuationInputError si WACC ≤ 0 o WACC − g < 1pp. Sustituye al recorte silencioso antiguo (EC-76).

    round(…, 9): 0.09 − 0.08 = 0.00999… en coma flotante; un spread de exactamente 1pp debe aceptarse.
    """
    if wacc <= 0:
        raise ValuationInputError(f"WACC must be > 0 (got {wacc:.2%}).")
    if round(wacc - terminal_growth, 9) < _MIN_WACC_MINUS_G:
        raise ValuationInputError(
            f"WACC ({wacc:.2%}) must exceed terminal growth ({terminal_growth:.2%}) by ≥ "
            f"{_MIN_WACC_MINUS_G:.0%}: Gordon TV = FCF/(WACC−g) explodes as the spread → 0."
        )


def fcff_breakdown(
    ocf: float, capex: float, interest_expense: float, tax_rate: float, sbc: float,
    treat_sbc_as_cash_cost: bool = True, interest_income: float = 0.0,
    finance_lease_principal: float = 0.0,
) -> FcffBreakdown:
    """
    FCFF = OCF + int×(1−t) − int_income×(1−t) − capex − SBC − principal de leasing financiero.

    - OCF en US GAAP ya restó los intereses pagados → se suman (netos de impuestos) para desapalancar.
    - OCF incluye los intereses cobrados sobre la caja → se restan, porque la caja se suma en el bridge.
    - Arrendamientos operativos (ASC 842): el pago ya está en el OCF → no se tocan aquí NI en el bridge.
    - Arrendamientos FINANCIEROS: el alta del ROU es no-caja y el principal va a financiación, así que
      CFO − capex nunca descuenta esa inversión → se resta el principal como capex equivalente.
    - SBC es remuneración real (Damodaran) → coste. Coherente con usar acciones diluidas hoy.
    Inputs en positivo (convención del parser): un negativo indica un bug de signo → fallo explícito.
    """
    for name, v in (("capex", capex), ("interest_expense", interest_expense), ("sbc", sbc),
                    ("interest_income", interest_income), ("finance_lease_principal", finance_lease_principal)):
        if v < 0:
            raise ValuationInputError(f"{name} must be ≥ 0 (got {v}); check XBRL sign convention.")
    if not 0.0 <= tax_rate < 1.0:
        raise ValuationInputError(f"tax_rate must be in [0, 1) (got {tax_rate}).")
    addback = interest_expense * (1 - tax_rate)
    income_strip = interest_income * (1 - tax_rate)
    sbc_charge = sbc if treat_sbc_as_cash_cost else 0.0
    return FcffBreakdown(
        ocf=ocf, after_tax_interest_addback=addback, after_tax_interest_income=income_strip,
        capex=capex, sbc_charge=sbc_charge, finance_lease_principal=finance_lease_principal,
        tax_rate=tax_rate,
        fcff=ocf + addback - income_strip - capex - sbc_charge - finance_lease_principal,
    )


def compute_fcff(
    ocf: float, capex: float, interest_expense: float, tax_rate: float, sbc: float,
    treat_sbc_as_cash_cost: bool = True, interest_income: float = 0.0,
    finance_lease_principal: float = 0.0,
) -> float:
    """Atajo escalar de fcff_breakdown()."""
    return fcff_breakdown(ocf, capex, interest_expense, tax_rate, sbc,
                          treat_sbc_as_cash_cost, interest_income, finance_lease_principal).fcff


def net_claims(
    total_debt: float, cash: float, sti: Optional[float] = None, lt_investments: Optional[float] = None,
    minority_interest: Optional[float] = None, preferred: Optional[float] = None,
) -> float:
    """Derechos netos de no-accionistas comunes sobre el EV.

    Una sola definición para los dos sentidos del puente: EV→equity (DCF, EPV) y
    equity de mercado→EV (múltiplos, reverse DCF, peers, critic). Si cada consumidor
    escribiera su "net debt" volveríamos a STI contado cero o dos veces (EC-81).
    """
    return (total_debt + (minority_interest or 0.0) + (preferred or 0.0)
            - cash - (sti or 0.0) - (lt_investments or 0.0))


def equity_bridge(
    enterprise_value: float, total_debt: float, cash: float,
    sti: Optional[float] = None, lt_investments: Optional[float] = None,
    lease_liabilities: Optional[float] = None, minority_interest: Optional[float] = None,
    preferred: Optional[float] = None,
) -> EquityBridge:
    """
    EV → equity común. Patrimonio contable negativo (AZO) es irrelevante: el bridge usa derechos, no book equity.
    Si el equity resultante es negativo se informa tal cual (fail loud), no se recorta a 0.
    lt_investments: SOLO no operativas (valores negociables). Las participaciones estratégicas generan
    EBIT propio y no deben sumarse — limitación del concepto XBRL; se anota en el informe.
    lease_liabilities: memo visible, NUNCA restado (ASC 842 — el pago ya reduce OCF/EBIT).
    """
    optional = {"short_term_investments": sti, "long_term_investments": lt_investments,
                "minority_interest": minority_interest, "preferred_stock": preferred}
    for name, v in {**optional, "total_debt": total_debt, "cash": cash}.items():
        if v is not None and v < 0:
            raise ValuationInputError(f"{name} must be ≥ 0 (got {v}).")
    claims = net_claims(total_debt, cash, sti, lt_investments, minority_interest, preferred)
    return EquityBridge(
        enterprise_value=enterprise_value, total_debt=total_debt,
        minority_interest=minority_interest or 0.0, preferred_stock=preferred or 0.0,
        cash=cash, short_term_investments=sti or 0.0, long_term_investments=lt_investments or 0.0,
        operating_lease_liabilities_memo=lease_liabilities,
        net_claims=claims, equity_value=enterprise_value - claims,
        # Ausente ≠ cero: el informe lista qué componentes se trataron como 0 por falta de dato.
        components_missing=[k for k, v in optional.items() if v is None],
    )


def per_share(equity_value: float, share_count: Optional[float]) -> float:
    """Equity / acciones. Fallo explícito si share_count ≤ 0 o ausente."""
    if share_count is None or share_count <= 0:
        raise ValuationInputError(f"share_count must be > 0 (got {share_count}).")
    return equity_value / share_count


def resolve_share_count(
    shares_outstanding: Optional[float], diluted_weighted: Optional[float],
    basic_weighted: Optional[float], market_cap: Optional[float] = None, price: Optional[float] = None,
) -> tuple[Optional[float], str]:
    """
    Nº de acciones diluido a fecha de cierre = portada × (diluidas media / básicas media).

    Por qué no la media diluida a secas: en empresas con recompras fuertes (AZO) la media del año
    supera el número a cierre → infravalora el per-share. El ratio aísla solo la dilución.
    Devuelve (None, "unavailable") si no hay ninguna fuente: el llamador NO debe inventar acciones.
    """
    if shares_outstanding and shares_outstanding > 0:
        if diluted_weighted and basic_weighted and basic_weighted > 0 and diluted_weighted >= basic_weighted:
            ratio = diluted_weighted / basic_weighted
            return shares_outstanding * ratio, f"shares_outstanding × diluted/basic ({ratio:.4f})"
        return shares_outstanding, "shares_outstanding (dilution not applied: basic/diluted unavailable)"
    if diluted_weighted and diluted_weighted > 0:
        return diluted_weighted, "diluted_weighted_average (overstates count for buyback-heavy issuers)"
    if market_cap and price and price > 0:
        return market_cap / price, "market_cap / price (approximation)"
    return None, "unavailable"


def dcf_components(
    base_fcf: float, g1: float, terminal_growth: float, wacc: float,
    stage1_years: int, fade_years: int = 0, mid_year: bool = False, *,
    nopat_base: Optional[float] = None, ronic: Optional[float] = None,
) -> DcfComponents:
    """
    DCF de 3 etapas (ADR-029) sobre FCFF → EV (ADR-033).

    Stage 1: g1 constante; Stage 2: fade lineal g1 → g; Stage 3: Gordon.
    Valor terminal con reinversión (Damodaran/Koller): si se da `nopat_base`, el flujo terminal es
    NOPAT_T × (1+g) × (1 − g/RONIC) con RONIC por defecto = WACC (conservador: el crecimiento
    terminal no crea valor). Sin `nopat_base`, flujo terminal = FCFF_T × (1+g) (legacy; sobrevalora
    el TV ~25 % con g 2,5 %, se marca `terminal_reinvestment_modeled=False`).
    mid_year=True multiplica por (1+WACC)^0.5 (exacto para Gordon; +4,4 % con WACC 9 %).
    Lanza ValuationInputError si WACC − g < 1pp (nunca recorta g en silencio, EC-76).
    """
    validate_dcf_rates(wacc, terminal_growth)
    pv_explicit, fcf, nopat = 0.0, base_fcf, nopat_base
    # Camino de crecimiento año a año: stage1 a g1, luego fade lineal (el último año del fade = g).
    growth_path = [g1] * stage1_years + [
        g1 - (g1 - terminal_growth) * fade_t / fade_years for fade_t in range(1, fade_years + 1)]
    for t, g_t in enumerate(growth_path, start=1):
        fcf *= 1 + g_t
        if nopat is not None:
            nopat *= 1 + g_t   # NOPAT crece al mismo ritmo: misma economía, distinta base
        pv_explicit += fcf / (1 + wacc) ** t
    if nopat is not None:
        r = ronic if ronic else wacc
        # g = tasa de reinversión × RONIC → reinversión = g/RONIC. Con g=0 no hay reinversión.
        reinvestment_rate = terminal_growth / r if r > 0 else 0.0
        terminal_flow = nopat * (1 + terminal_growth) * (1 - reinvestment_rate)
    else:
        terminal_flow = fcf * (1 + terminal_growth)
    n = stage1_years + fade_years
    pv_terminal = terminal_flow / (wacc - terminal_growth) / (1 + wacc) ** n
    factor = (1 + wacc) ** 0.5 if mid_year else 1.0
    total = (pv_explicit + pv_terminal) * factor
    return DcfComponents(
        pv_explicit=pv_explicit * factor, pv_terminal=pv_terminal * factor, enterprise_value=total,
        terminal_value_share=(pv_terminal * factor / total) if total != 0 else None, mid_year=mid_year,
        terminal_reinvestment_modeled=nopat_base is not None,
    )


# --------------------------------------------------------------------------- #
# WACC real (T11): wE·Ke + wD·Kd·(1−t), con Kd por rating sintético
# --------------------------------------------------------------------------- #

# Rating sintético (Damodaran, tabla "large firms"): cobertura EBIT/intereses → spread sobre Rf.
# Determinista, simple y refleja el coste MARGINAL de la deuda (un cupón histórico bajo, emitido
# con tipos al 1 %, no sirve para descontar flujos futuros).
# VERIFY against Damodaran ratings.htm (Jan 2026) — pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.htm.
# Valores tomados del plan H1 sin verificación online (no hay red en la implementación); actualizar
# spreads y fecha cuando se revise. Cada fila: (cobertura mínima, spread, rating).
_SYNTHETIC_RATING_TABLE: tuple[tuple[float, float, str], ...] = (
    # Etiquetas alineadas con los tramos de cobertura de Damodaran (revisión final H1, #7: estaban
    # desplazadas una fila — 7× salía "A+" cuando es AA). Los spreads no cambian.
    (8.50, 0.006, "AAA"), (6.50, 0.008, "AA"), (5.50, 0.010, "A+"), (4.25, 0.011, "A"),
    (3.00, 0.013, "A-"), (2.50, 0.017, "BBB"), (2.25, 0.023, "BB+"), (2.00, 0.030, "BB"),
    (1.75, 0.040, "B+"), (1.50, 0.050, "B"), (1.25, 0.065, "B-"), (0.80, 0.080, "CCC"),
    (0.65, 0.100, "CC"), (0.20, 0.130, "C"), (float("-inf"), 0.170, "D"),
)


def estimate_pre_tax_cost_of_debt(
    *, ebit: Optional[float], interest_expense: Optional[float], rf: float = RF_PCT / 100,
) -> tuple[float, str]:
    """Kd = Rf + spread(rating sintético por cobertura de intereses). Fallback Rf + 2pp si faltan datos.

    Sustituye a 'intereses / deuda media' (cupón histórico, no marginal; necesitaba clamps ad hoc).
    Devuelve (kd_fracción, etiqueta de fuente legible para el informe).
    """
    if ebit is None or interest_expense is None:
        return rf + _DEFAULT_CREDIT_SPREAD, f"fallback Rf+{_DEFAULT_CREDIT_SPREAD:.0%} (EBIT or interest unavailable)"
    if interest_expense <= 0:
        # Sin intereses el peso de la deuda será ~0: el Kd elegido apenas afecta al WACC.
        return rf + _DEFAULT_CREDIT_SPREAD, "no interest expense (weight ≈ 0 — irrelevant)"
    coverage = ebit / interest_expense
    for floor, spread, rating in _SYNTHETIC_RATING_TABLE:
        if coverage >= floor:
            label = f"coverage {coverage:.1f}x" if coverage >= 0.2 else "coverage <0.2x"
            return rf + spread, f"synthetic rating {rating} ({label}) → Rf+{spread:.1%}"
    return rf + 0.17, "synthetic rating D"   # inalcanzable (última fila = -inf); defensa explícita


def compute_wacc(
    cost_of_equity: float, pre_tax_cost_of_debt: float, tax_rate: float,
    equity_value: Optional[float], debt_value: float, *,
    cost_of_equity_source: str = "", cost_of_debt_source: str = "",
) -> WaccBreakdown:
    """
    WACC = E/(D+E)·Ke + D/(D+E)·Kd·(1−t). E = equity de MERCADO (nunca el book equity: AZO es
    negativo). D = deuda contable como proxy del valor de mercado (sin arrendamientos operativos,
    coherente con ASC 842 y el bridge). Fail loud si E ≤ 0 o D < 0.
    """
    if equity_value is None or equity_value <= 0:
        raise ValuationInputError(f"Market equity must be > 0 for WACC weights (got {equity_value}).")
    if debt_value < 0:
        raise ValuationInputError(f"debt_value must be ≥ 0 (got {debt_value}).")
    total = equity_value + debt_value
    we, wd = equity_value / total, debt_value / total
    kd_at = pre_tax_cost_of_debt * (1 - tax_rate)   # escudo fiscal: los intereses deducen
    return WaccBreakdown(
        cost_of_equity=cost_of_equity, cost_of_equity_source=cost_of_equity_source,
        pre_tax_cost_of_debt=pre_tax_cost_of_debt, cost_of_debt_source=cost_of_debt_source,
        tax_rate=tax_rate, after_tax_cost_of_debt=kd_at, equity_weight=we, debt_weight=wd,
        wacc=we * cost_of_equity + wd * kd_at,
    )


def describe_wacc(w: WaccBreakdown) -> str:
    """Desglose legible del WACC para checkpoint 2, CLI, archiver y Value Expert (una sola redacción).

    Ej.: "Ke 9.2% (CAPM ...) × 87% + Kd 4.8% (synthetic rating AAA ...) × (1−25%) × 13% = 8.5%".
    """
    ke_src = f" ({w.cost_of_equity_source})" if w.cost_of_equity_source else ""
    kd_src = f" ({w.cost_of_debt_source})" if w.cost_of_debt_source else ""
    return (
        f"Ke {w.cost_of_equity:.1%}{ke_src} × {w.equity_weight:.0%} + "
        f"Kd {w.pre_tax_cost_of_debt:.1%}{kd_src} × (1−{w.tax_rate:.0%}) × {w.debt_weight:.0%} "
        f"= {w.wacc:.1%}"
    )
