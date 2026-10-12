"""
Tests del bloque A de la valoración (plan 2026-10-11, ADR-043).

A1: la base del DCF es la mediana del margen FCFF de 5 años (como el EPV, que ya usaba 5 años de
EBIT) y el crecimiento de la etapa 1 es el CAGR de ventas, coherente con esa base: con un margen
normalizado constante, el FCFF crece como las ventas. Antes, g1 = CAGR del FCF entre dos años
extremos: KO salía a −9,4 % por dos años con pagos extraordinarios aunque sus ventas crecían al 7,7 %.
"""
import pytest

from due_diligence.agents.valuation import _build_fcff, _compute_near_term_growth
from due_diligence.schemas.base import FinancialsReport, MetricValue


def _mv(v: float, period: str = "FY2025") -> MetricValue:
    return MetricValue(value=v, unit="USD", period=period, source="XBRL")


def _fin(ocf: list[float], revenue: list[float], fcf: list[float] | None = None) -> FinancialsReport:
    """FinancialsReport mínimo: OCF e ingresos por año (más reciente primero), sin capex ni intereses."""
    years = [f"FY{2025 - i}" for i in range(len(ocf))]
    return FinancialsReport(
        ticker="TEST", cik="0000000001", as_of_date="2025-12-31", source_filing="10-K FY2025",
        # Margen operativo constante (20 %): la empresa NO es cíclica (ADR-034), así la base es la no cíclica.
        operating_income=[_mv(r * 0.2, y) for r, y in zip(revenue, years)],
        net_income=[_mv(r * 0.15, y) for r, y in zip(revenue, years)],
        gross_profit=[_mv(r * 0.4, y) for r, y in zip(revenue, years)],
        total_assets=_mv(1000.0), total_debt=_mv(0.0), cash_and_equivalents=_mv(0.0),
        revenue=[_mv(r, y) for r, y in zip(revenue, years)],
        operating_cash_flow=[_mv(o, y) for o, y in zip(ocf, years)],
        capex=[_mv(0.0, y) for y in years], interest_expense=[_mv(0.0, y) for y in years],
        sbc=[_mv(0.0, y) for y in years], interest_income=[_mv(0.0, y) for y in years],
        free_cash_flow=[_mv(f, y) for f, y in zip(fcf or ocf, years)],
    )


# --------------------------------------------------------------------------- #
# A1 — base normalizada de 5 años
# --------------------------------------------------------------------------- #

def test_fcff_base_uses_five_year_median_margin():
    """KO-like: márgenes 12 %, 10,6 %, 21,9 %, 22,2 %, 31,1 % → mediana de 5 años 21,9 %, no 12 %."""
    rev = [100.0] * 5
    _, base, _, _, source = _build_fcff(_fin([12.0, 10.6, 21.9, 22.2, 31.1], rev), 0.25)
    assert base == pytest.approx(21.9)
    assert "median FCFF margin (5y)" in source


def test_fcff_base_with_three_years_uses_three():
    _, base, _, _, source = _build_fcff(_fin([10.0, 9.0, 7.75], [100.0] * 3), 0.25)
    assert base == pytest.approx(9.0)
    assert "median FCFF margin (3y)" in source


def test_fcff_base_scales_median_margin_by_latest_revenue():
    """Margen 10 % los 5 años; ventas actuales 200 → base 20."""
    _, base, _, _, _ = _build_fcff(_fin([20.0, 15.0, 10.0, 8.0, 5.0], [200.0, 150.0, 100.0, 80.0, 50.0]), 0.25)
    assert base == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# A1 — crecimiento coherente con la base
# --------------------------------------------------------------------------- #

def test_growth_uses_revenue_cagr_not_noisy_fcf_cagr():
    """FCF de 9,75 → 4,74 (caída puntual) con ventas al 7,7 %: g1 = 7,7 %."""
    fin = _fin([4.74, 9.75], [100.0, 93.0], fcf=[4.74, 9.75])
    g, src = _compute_near_term_growth(fin, 0.077, None, revenue_cagr_years=5)
    assert g == pytest.approx(0.077) and src == "revenue_cagr_5y"


def test_growth_source_reports_actual_revenue_window():
    g, src = _compute_near_term_growth(None, 0.05, None, revenue_cagr_years=3)
    assert src == "revenue_cagr_3y"


def test_auto_growth_capped_at_fifteen_percent():
    """Base rates (Mauboussin): el crecimiento de ventas persiste poco; el automático no pasa del 15 %."""
    g, src = _compute_near_term_growth(None, 0.25, None)
    assert g == pytest.approx(0.15) and src == "revenue_cagr_5y_capped"


def test_auto_growth_floor_minus_fifteen_percent():
    g, src = _compute_near_term_growth(None, -0.30, None)
    assert g == pytest.approx(-0.15) and src == "revenue_cagr_5y_capped"


def test_negative_revenue_growth_is_kept_for_declining_business():
    g, src = _compute_near_term_growth(None, -0.04, None)
    assert g == pytest.approx(-0.04) and src == "revenue_cagr_5y"


def test_user_override_keeps_its_own_wider_bounds():
    """El inversor puede justificar más del 15 % en el checkpoint 2 (techo 30 %, como antes)."""
    g, src = _compute_near_term_growth(None, 0.05, 25.0)
    assert g == pytest.approx(0.25) and src == "user_override"


def test_fcf_cagr_only_when_no_revenue_cagr():
    """Sin CAGR de ventas: CAGR del FCF (acotado igual que el automático)."""
    fin = _fin([12.1, 11.0, 10.0], [100.0] * 3, fcf=[12.1, 11.0, 10.0])
    fin = fin.model_copy(update={"revenue": fin.revenue[:1]})    # sin serie de ventas suficiente
    g, src = _compute_near_term_growth(fin, None, None)
    assert src == "fcf_cagr_2y" and g == pytest.approx(0.1, abs=1e-3)


def test_default_seven_percent_without_any_history():
    assert _compute_near_term_growth(None, None, None) == (pytest.approx(0.07), "default_7pct")


# --------------------------------------------------------------------------- #
# A3 — DCF en tres escenarios con supuestos de la propia historia
# --------------------------------------------------------------------------- #

from due_diligence.agents.valuation import _dcf_scenarios  # noqa: E402


def _rev_series(values: list[float]) -> list[MetricValue]:
    return [_mv(v, f"FY{2025 - i}") for i, v in enumerate(values)]


def test_scenarios_use_margin_quartiles_and_growth_range_from_history():
    # Márgenes 10/12/20/22/31 % → P25 12 %, mediana 20 %, P75 22 % (cuantiles inclusivos).
    # Ventas 11 años: CAGR 3a = (133.1/100)^(1/3)−1 = 10 %; 5a ≈ 6,0 %; 10a ≈ 3,6 % (valores abajo).
    rev = _rev_series([133.1, 121.0, 110.0, 100.0, 100.0, 100.0, 100.0, 100.0, 95.0, 95.0, 93.8])
    calls = []
    fn = lambda base, g, scale=1.0: (calls.append((round(base, 4), round(g, 4))) or base * 10 + g, None)  # noqa: E731
    out = _dcf_scenarios([0.10, 0.12, 0.20, 0.22, 0.31], rev, g_base=0.06, cyclical=False, value_fn=fn)
    assert [s.name for s in out] == ["bear", "base", "bull"]
    assert [s.fcff_margin for s in out] == pytest.approx([0.12, 0.20, 0.22])
    g = [s.near_term_growth for s in out]
    assert g[1] == pytest.approx(0.06) and g[0] < g[1] < g[2]
    assert g[2] == pytest.approx(0.10, abs=1e-4)           # máximo: CAGR a 3 años
    # base_fcf = margen × ventas actuales (133,1)
    assert calls[1] == (round(0.20 * 133.1, 4), 0.06)


def test_scenarios_growth_bounded_like_automatic_growth():
    rev = _rev_series([200.0, 100.0, 90.0, 80.0])          # CAGR 3a ≈ 35,7 % → acotado a 15 %
    out = _dcf_scenarios([0.1, 0.1, 0.1], rev, g_base=0.05, cyclical=False, value_fn=lambda b, g, scale=1.0: (g, None))
    assert out[2].near_term_growth == pytest.approx(0.15)


def test_cyclical_scenarios_vary_margin_only():
    rev = _rev_series([150.0, 100.0, 120.0, 90.0])
    out = _dcf_scenarios([0.05, 0.1, 0.3, 0.2], rev, g_base=0.025, cyclical=True, value_fn=lambda b, g, scale=1.0: (b, None))
    assert {s.near_term_growth for s in out} == {0.025}
    assert out[0].fcff_margin < out[1].fcff_margin < out[2].fcff_margin


def test_scenarios_need_three_margin_years():
    assert _dcf_scenarios([0.1, 0.2], _rev_series([1.0, 1.0]), g_base=0.05, cyclical=False,
                          value_fn=lambda b, g, scale=1.0: (b, None)) == []


def test_base_scenario_reproduces_headline_dcf_per_share():
    """Mismo camino que el titular (bridge + capitalización + reinversión terminal)."""
    from due_diligence.agents.valuation import valuation_agent
    from tests.test_valuation import _levered_state
    st = _levered_state()
    years = [f"FY{2025 - i}" for i in range(5)]
    rev = [2000e6, 1900e6, 1800e6, 1700e6, 1600e6]
    ocf = [260e6, 240e6, 250e6, 200e6, 210e6]
    st["financials"] = st["financials"].model_copy(update={
        "revenue": [_mv(v, y) for v, y in zip(rev, years)],
        "operating_cash_flow": [_mv(v, y) for v, y in zip(ocf, years)],
        "capex": [_mv(60e6, y) for y in years], "interest_expense": [_mv(40e6, y) for y in years],
        "sbc": [_mv(20e6, y) for y in years],
        "operating_income": [_mv(300e6, y) for y in years]})
    st["checkpoint2_user_input"]["revenue_growth_yr1_pct"] = None
    st["historical_cagr_5y"], st["historical_cagr_years"] = 0.0573, 4
    r = valuation_agent(st)["valuation"]
    s = {x.name: x for x in r.dcf_scenarios}
    assert s["base"].per_share == pytest.approx(r.dcf_intrinsic_per_share.value, abs=0.01)
    assert s["bear"].per_share <= s["base"].per_share <= s["bull"].per_share
    assert s["base"].upside_pct == pytest.approx(r.dcf_upside_pct, abs=0.1)


# --------------------------------------------------------------------------- #
# A1 (revisión) — tendencia por mínimos cuadrados, robusta a los extremos
# --------------------------------------------------------------------------- #

from due_diligence.utils.periods import least_squares_growth  # noqa: E402


def test_least_squares_growth_recovers_exact_geometric_growth():
    rev = _rev_series([100 * 1.08 ** (9 - i) for i in range(10)])     # 8 % exacto, 10 puntos
    g, n = least_squares_growth(rev)
    assert g == pytest.approx(0.08) and n == 9


def test_least_squares_growth_resists_a_trough_start():
    """XOM-like: inicio en un mínimo (2020) y rebote → el CAGR extremo exagera; la tendencia no."""
    vals = [340.0, 345.0, 350.0, 413.0, 285.0, 181.0, 265.0, 290.0, 244.0, 226.0]  # 2025→2016
    g, _ = least_squares_growth(_rev_series(vals))
    assert 0.03 < g < 0.08


def test_least_squares_growth_needs_three_points():
    assert least_squares_growth(_rev_series([110.0, 100.0])) is None


def test_growth_prefers_revenue_trend_when_series_available():
    fin = _fin([10.0] * 10, [100 * 1.06 ** (9 - i) for i in range(10)])
    g, src = _compute_near_term_growth(fin, 0.20, None)          # el CAGR 5a (20 %) se ignora
    assert g == pytest.approx(0.06) and src == "revenue_trend_9y"


# --------------------------------------------------------------------------- #
# Correcciones de la revisión de código del bloque A
# --------------------------------------------------------------------------- #

def test_scenarios_scale_terminal_with_margin():
    """#1: el valor terminal también refleja el margen del escenario (si no, el rango sale estrecho)."""
    rev = _rev_series([100.0, 100.0, 100.0, 100.0, 100.0])
    seen = []
    _dcf_scenarios([0.10, 0.12, 0.20, 0.22, 0.31], rev, g_base=0.05, cyclical=False,
                   value_fn=lambda base, g, scale=1.0: (seen.append(round(scale, 4)) or 1.0, None))
    assert seen == [round(0.12 / 0.20, 4), 1.0, round(0.22 / 0.20, 4)]


def test_scenarios_never_put_bull_below_base_with_user_override():
    """#2: override del 25 % con historia al 8 % → optimista ≥ base (antes: 15 % < 25 %)."""
    rev = _rev_series([108.0, 100.0, 92.6, 85.7])
    out = _dcf_scenarios([0.1, 0.1, 0.1], rev, g_base=0.25, cyclical=False, value_fn=lambda b, g, scale=1.0: (g, None))
    assert out[2].near_term_growth >= out[1].near_term_growth == pytest.approx(0.25)


def test_least_squares_growth_tolerates_non_fiscal_labels():
    """#4: etiquetas no FY (datos sintéticos) → None y se cae al CAGR, nunca una excepción."""
    series = [MetricValue(value=v, unit="USD", period=p, source="x")
              for v, p in ((110.0, "2025"), (100.0, "2024"), (90.0, "2023"))]
    assert least_squares_growth(series) is None
