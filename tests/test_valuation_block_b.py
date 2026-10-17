"""
Tests del bloque B, B1 (ADR-044): decisión por rentabilidad frente a un listón.

Propiedades, no cifras sueltas: (1) ida y vuelta — al precio de entrada la rentabilidad del caso base es
el listón; (2) el precio de entrada no depende del precio de mercado (solo de los flujos y del listón);
(3) el caso pesimista puede fijar la entrada; (4) EPV y NOPAT terminal parten del margen normalizado;
(5) ciclicidad: "en pérdidas" y "no evaluada" ya no son un "no cíclica" silencioso.
"""
import pytest

from due_diligence.agents.valuation import valuation_agent
from due_diligence.agents.valuation_math import assess_cyclicality, solve_decreasing, wacc_for_cost_of_equity
from due_diligence.schemas.base import FinancialsReport, MetricValue
from due_diligence.schemas.reports import MarketReport, WaccBreakdown

_YEARS = ("FY2025", "FY2024", "FY2023", "FY2022", "FY2021")


def _series(values, unit="USD"):
    return [MetricValue(value=v, unit=unit, period=p, source="test") for v, p in zip(values, _YEARS)]


def _financials(ocf=(260e6, 250e6, 230e6, 220e6, 180e6), ebit=(300e6, 290e6, 270e6, 250e6, 230e6)):
    rev = (2_000e6, 1_900e6, 1_800e6, 1_700e6, 1_600e6)
    return FinancialsReport(
        ticker="LEV", cik="0000000001", as_of_date="2025-12-31", source_filing="10-K FY2025",
        revenue=_series(rev), gross_profit=_series([r * 0.4 for r in rev]), operating_income=_series(ebit),
        net_income=_series([e * 0.65 for e in ebit]), operating_cash_flow=_series(ocf),
        capex=_series([60e6] * 5), free_cash_flow=_series([o - 60e6 for o in ocf]),
        interest_expense=_series([40e6] * 5), sbc=_series([20e6] * 5),
        total_assets=MetricValue(value=3_000e6, unit="USD", period="FY2025", source="test"),
        total_debt=MetricValue(value=500e6, unit="USD", period="FY2025", source="test"),
        cash_and_equivalents=MetricValue(value=50e6, unit="USD", period="FY2025", source="test"),
        shares_outstanding=MetricValue(value=10e6, unit="shares", period="FY2025", source="test"),
    )


def _state(price=150.0, **fin):
    return {
        "ticker": "LEV", "financials": _financials(**fin),
        "market": MarketReport(ticker="LEV", stub=False,
                               price_current=MetricValue(value=price, unit="USD", period="2026-10-01", source="t"),
                               market_cap=MetricValue(value=1_500e6, unit="USD", period="2026-10-01", source="t")),
        "checkpoint2_user_input": {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10,
                                   "mid_year_convention": False, "valuation_date": "2025-12-31"},
    }


def _val(**kw):
    return valuation_agent(_state(**kw))["valuation"]


# --- matemática pura ---
def test_wacc_for_cost_of_equity_keeps_debt_cost_and_weights():
    w = WaccBreakdown(cost_of_equity=0.09, cost_of_equity_source="", pre_tax_cost_of_debt=0.06, cost_of_debt_source="",
                      tax_rate=0.25, after_tax_cost_of_debt=0.045, equity_weight=0.8, debt_weight=0.2, wacc=0.081)
    assert wacc_for_cost_of_equity(0.12, w) == pytest.approx(0.8 * 0.12 + 0.2 * 0.045)
    assert wacc_for_cost_of_equity(0.12, None) == 0.12


def test_solve_decreasing_finds_the_rate_and_reports_bounds():
    r, status = solve_decreasing(lambda x: 100 / x, 1_000.0, 0.01, 0.5)
    assert status == "ok" and r == pytest.approx(0.1, abs=1e-5)
    assert solve_decreasing(lambda x: 100 / x, 10.0, 0.01, 0.5) == (None, "above")     # incluso a 50 % vale más
    assert solve_decreasing(lambda x: 100 / x, 50_000.0, 0.01, 0.5) == (None, "below")


# --- rentabilidad implícita y precio de entrada ---
def test_hurdle_is_never_below_the_market_required_return():
    v = _val()
    assert v.hurdle_rate == pytest.approx(max(0.10, 0.042 + 0.05))   # Rf estático 4,2 % (sin FRED en tests)
    assert "investor hurdle" in v.hurdle_source


def test_round_trip_at_the_entry_price_the_base_case_returns_the_hurdle():
    v = _val()
    assert v.entry_price and "base case" in v.entry_basis
    at_entry = _val(price=v.entry_price)
    assert at_entry.implied_return == pytest.approx(v.hurdle_rate, abs=2e-4)


def test_entry_price_does_not_depend_on_the_market_price():
    assert _val(price=100.0).entry_price == pytest.approx(_val(price=250.0).entry_price, rel=1e-9)


def test_lower_price_means_higher_expected_return_and_scenarios_are_ordered():
    cheap, dear = _val(price=100.0), _val(price=250.0)
    assert cheap.implied_return > dear.implied_return
    bear, base, bull = (next(s for s in cheap.dcf_scenarios if s.name == n) for n in ("bear", "base", "bull"))
    assert bear.implied_return <= base.implied_return <= bull.implied_return
    assert base.implied_return == cheap.implied_return            # el base ES el titular


def test_bear_case_can_set_the_entry_price():
    # Dos años de FCFF flojos bajan el P25 del margen: el pesimista exige una entrada más baja que el base.
    v = _val(ocf=(260e6, 250e6, 80e6, 80e6, 180e6))
    assert "bear case returns the risk-free" in v.entry_basis and v.entry_price > 0


def test_worthless_bear_case_means_no_entry_price_and_says_why():
    v = _val(ocf=(260e6, 250e6, 40e6, 30e6, 180e6))   # P25 del margen casi nulo: equity ≤ 0 en el pesimista
    assert v.entry_price is None and "no price passes the downside test" in v.entry_basis


def test_expected_return_above_the_search_bound_is_reported_not_invented():
    v = _val(price=1.0)
    assert v.implied_return is None and "above 60%" in v.implied_return_note


# --- EPV y NOPAT terminal sobre el margen normalizado (S3 de la revisión 2026-10-17) ---
def test_epv_uses_median_margin_times_latest_revenue_for_growers():
    v = _val()
    # Márgenes EBIT 15 %, 15,3 %, 15 %, 14,7 %, 14,4 % → mediana 15 % × 2 000 M = 300 M (la mediana en
    # dólares de 5 años habría sido 270 M: infravalora a quien crece).
    assert v.epv_source == "ebit_margin_median_5y"
    expected_ev = 300e6 * 0.75 / 0.09
    assert v.epv_enterprise.value == pytest.approx(expected_ev, rel=1e-3)


# --- ciclicidad ---
def _margins(values):
    return [(f"FY{2025 - i}", m) for i, m in enumerate(values)]


def test_loss_making_is_not_a_cycle():
    c = assess_cyclicality(_margins([0.03, -0.2, -0.4, -0.1, -0.3]))    # PATH: pérdidas con varianza
    assert c.is_cyclical is False and c.loss_making is True and "loss-making" in c.evidence


def test_insufficient_history_is_marked_not_assessed():
    c = assess_cyclicality(_margins([0.29]))                            # JNJ: un solo año de EBIT en XBRL
    assert c.is_cyclical is False and c.assessed is False


def test_peak_rule_unchanged_for_real_cyclicals_and_one_off_troughs():
    assert assess_cyclicality(_margins([0.36, 0.08, 0.31, 0.07, 0.07, 0.05, 0.075, 0.06, 0.08, -0.12])).is_cyclical
    # BBW: el COVID es un valle aislado, no un ciclo (calibración EC-92 que el test de valles rompía).
    assert not assess_cyclicality(_margins([0.13, 0.12, 0.13, -0.08, 0.004, 0.12])).is_cyclical


# --- ruptura estructural: solo aviso ---
def test_structural_break_warns_but_does_not_change_growth():
    st = _state()
    st["historical_cagr_5y"], st["historical_cagr_years"] = 0.12, 5    # tendencia ~5,7 % frente a 12 %
    v = valuation_agent(st)["valuation"]
    assert v.growth_structural_break and "structural break" in v.growth_structural_break
    assert v.near_term_growth_source.startswith("revenue_trend_")


def test_cyclical_bear_uses_multi_year_averages_not_a_single_trough_year():
    # CALM (2026-10-17): perpetuar un año valle (−2,5 %) dejaba el equity casi a cero con 0,9 B$ de caja.
    from due_diligence.agents.valuation import _dcf_scenarios
    margins = [0.10, 0.24, 0.12, 0.22, 0.03, -0.03, 0.01, 0.05, 0.02, 0.08]   # 10 años, más reciente primero
    rev = [MetricValue(value=4_000e6, unit="USD", period="FY2026", source="t")]
    out = _dcf_scenarios(margins, rev, g_base=0.025, cyclical=True, value_fn=lambda fcf, g, scale: (fcf, None))
    bear, base, bull = (next(s for s in out if s.name == n) for n in ("bear", "base", "bull"))
    from statistics import quantiles
    rolling = [sum(margins[i:i + 3]) / 3 for i in range(8)]
    assert bear.fcff_margin == pytest.approx(quantiles(rolling, n=4, method="inclusive")[0])   # P25 de las medias
    assert bear.fcff_margin > min(margins) and bear.fcff_margin > 0          # nunca el peor año suelto
    assert base.fcff_margin == pytest.approx(0.065)                         # mediana de años sueltos (titular)
    assert bear.fcff_margin <= base.fcff_margin <= bull.fcff_margin
    # Una no cíclica sigue con años sueltos.
    plain = _dcf_scenarios(margins[:5], rev, g_base=0.05, cyclical=False, value_fn=lambda f, g, s: (f, None))
    assert next(s for s in plain if s.name == "bear").fcff_margin == pytest.approx(0.10)


def test_bear_case_is_still_tested_when_the_discount_floor_exceeds_the_risk_free_rate():
    # Revisión del bloque B (#1): con g terminal 3,5 % el suelo de Ke (g + 1 pp ≈ 4,5 %) supera a Rf 4,2 %.
    # Antes el pesimista dejaba de evaluarse y el informe decía "no bear scenario".
    st = _state(ocf=(260e6, 250e6, 60e6, 60e6, 180e6))
    st["checkpoint2_user_input"]["terminal_growth_pct"] = 3.5
    v = valuation_agent(st)["valuation"]
    assert "no bear scenario" not in v.entry_basis
    assert "bear case returns" in v.entry_basis and "lowest valid discount rate" in v.entry_basis


def test_loss_making_history_keeps_the_latest_ebit_instead_of_a_negative_normalized_one():
    from due_diligence.agents.valuation import _normalized_ebit
    fin = _financials(ebit=(300e6, -200e6, -250e6, -100e6, -150e6))   # giro reciente a beneficios
    assert _normalized_ebit(fin, None) == (None, "")
