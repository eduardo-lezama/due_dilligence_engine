"""
Ventanas de las series anuales fijadas por test (T7, revisión funcional).

Por qué: con periods=10 (EC-79) las series pasan de 4 a hasta 10 años. Sin tope
explícito, cada consumidor que usaba "la serie completa" cambiaría de ventana en
silencio (CAGR de 9 años como growth de stage 1, tendencia ROIC a 10 años...).
Estos tests fijan cada ventana con una FinancialsReport sintética de 10 años.
"""
from datetime import date

import pytest

from due_diligence.schemas.base import FinancialsReport, MetricValue

YEARS = list(range(2025, 2015, -1))  # FY2025..FY2016, index 0 = latest


def _series(values, unit="USD"):
    return [MetricValue(value=v, unit=unit, period=f"FY{y}", source="XBRL")
            for y, v in zip(YEARS, values)]  # zip aceptado: construye UNA serie (año, valor)


def _ten_year_financials(**over) -> FinancialsReport:
    rev = [200.0 * (0.95 ** i) for i in range(10)]
    base = dict(
        ticker="TEN", cik="0000000001", as_of_date=date(2025, 12, 31),
        revenue=_series(rev), gross_profit=_series([r * 0.4 for r in rev]),
        operating_income=_series([r * 0.2 for r in rev]), net_income=_series([r * 0.15 for r in rev]),
        operating_cash_flow=_series([r * 0.25 for r in rev]), capex=_series([r * 0.05 for r in rev]),
        free_cash_flow=_series([r * 0.2 for r in rev]),
        total_assets=MetricValue(value=1000.0, period="FY2025", source="XBRL"),
        total_debt=MetricValue(value=100.0, period="FY2025", source="XBRL"),
        cash_and_equivalents=MetricValue(value=50.0, period="FY2025", source="XBRL"),
        source_filing="10-K FY2025",
    )
    base.update(over)
    return FinancialsReport(**base)


def test_revenue_cagr_uses_five_fiscal_years():
    from due_diligence.agents.financials import _compute_revenue_cagr_5y
    fin = _ten_year_financials()
    cagr, n = _compute_revenue_cagr_5y(fin.revenue)
    assert n == 5
    assert cagr == pytest.approx((200.0 / (200.0 * 0.95 ** 5)) ** (1 / 5) - 1)


def test_fcf_cagr_window_capped_at_five_years():
    from due_diligence.agents.valuation import _fcf_cagr_with_window
    fin = _ten_year_financials()
    _, n = _fcf_cagr_with_window(fin.free_cash_flow)
    assert 0 < n <= 5


def test_ebit_median_uses_latest_five():
    from due_diligence.agents.valuation import _ebit_median
    fin = _ten_year_financials()
    med, label = _ebit_median(fin.operating_income)
    assert label == "ebit_median_5y"
    assert med == pytest.approx(sorted(m.value for m in fin.operating_income[:5])[2])


def test_value_expert_roic_trend_uses_five_year_window():
    """roic_series[-1] (10 años) → roic_series[min(4, len-1)] (5 años)."""
    from due_diligence.agents.value_expert import _format_context
    # latest 20 %, hace 4 años 10 % (RISING a 5 años), hace 9 años 40 % (FALLING a 10 años)
    roic = [0.20, 0.18, 0.15, 0.12, 0.10, 0.20, 0.25, 0.30, 0.35, 0.40]
    fin = _ten_year_financials(roic_series=_series(roic, unit="ratio"))
    ctx = _format_context({"ticker": "TEN", "financials": fin})
    assert "RISING" in ctx and "over 4 years" in ctx


def test_cyclical_peak_median_uses_latest_five_years():
    """rev_median sobre 5 años: 200 vs mediana 180 (1.11x) → sin aviso de pico.
    Con la serie de 10 años (mediana 160 → 1.25x) habría disparado en falso."""
    from due_diligence.agents.valuation import valuation_agent
    from due_diligence.schemas.reports import MarketReport
    rev = [200.0, 190.0, 180.0, 170.0, 160.0, 100.0, 100.0, 100.0, 100.0, 100.0]
    fin = _ten_year_financials(revenue=_series([v * 1e6 for v in rev]),
                               free_cash_flow=_series([v * 0.2e6 for v in rev]),
                               operating_income=_series([v * 0.2e6 for v in rev]))
    market = MarketReport(ticker="TEN", stub=False,
                          price_current=MetricValue(value=10.0, period="2026-01-01", source="Polygon"),
                          market_cap=MetricValue(value=400e6, period="2026-01-01", source="Polygon"))
    rep = valuation_agent({"ticker": "TEN", "financials": fin, "market": market})["valuation"]
    assert "Cyclical peak base" not in (rep.dcf_reliability_note or "")


def test_margins_aligned_by_period_not_position():
    """Critic/Value Expert: margen bruto = GP/Revenue del MISMO año (EC-82), aunque una
    serie tenga hueco. GP sin FY2024 → el margen FY2023 no puede usar el revenue FY2024."""
    from due_diligence.agents.value_expert import _format_context
    rev = _series([100.0, 200.0, 100.0, 100.0])  # FY2024 distinto: delata el emparejamiento posicional
    gp = [MetricValue(value=40.0, period="FY2025", source="XBRL"),
          MetricValue(value=30.0, period="FY2023", source="XBRL"),
          MetricValue(value=20.0, period="FY2022", source="XBRL")]
    fin = _ten_year_financials(revenue=rev, gross_profit=gp)
    ctx = _format_context({"ticker": "TEN", "financials": fin})
    assert "Gross margin trend (oldest→latest): 20.0% → 30.0% → 40.0%" in ctx
