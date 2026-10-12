"""
Tests de la normalización cíclica de la valoración (EC-92).

Una cíclica (márgenes con picos muy por encima de su nivel normal: acero, fertilizantes,
huevos) no se puede valorar extrapolando sus últimos años: el análisis de CALM daba un DCF
de $205/acción con el precio en $66,67 porque partía de un año de gripe aviar y proyectaba
un 16,6 % de crecimiento sacado de ese mismo pico. Aquí se prueba el detector (calibrado con
datos reales de EDGAR de 7 empresas) y que DCF, EPV, valor terminal y reverse DCF usan el
mismo nivel "mid-cycle" cuando la empresa es cíclica.
"""
import json
from pathlib import Path

import pytest

from due_diligence.agents.archiver import _render_markdown
from due_diligence.agents.valuation import (
    _build_fcff,
    _compute_near_term_growth,
    _cyclicality,
    valuation_agent,
)
from due_diligence.agents.valuation_math import assess_cyclicality, dcf_components
from due_diligence.agents.value_expert import _format_context
from due_diligence.schemas.base import FinancialsReport, MetricValue
from due_diligence.schemas.reports import MarketReport
from due_diligence.tools.edgar import EdgarAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _margins(values_pct: list[float], last_year: int = 2025) -> list[tuple[str, float]]:
    """Lista (periodo, margen) con el más reciente primero, como la produce _cyclicality."""
    return [(f"FY{last_year - i}", v / 100) for i, v in enumerate(values_pct)]


# --- Detector: márgenes operativos reales de 10 años (EDGAR, medidos 2026-10-09) ---
_REAL_PROFILES = {
    "AAPL": ([32.0, 31.5, 29.8, 30.3, 29.8, 24.1, 24.6, 26.7, 26.8, 27.8], False),
    "AZO": ([19.1, 20.5, 19.9, 20.1, 20.1, 19.1, 18.7, 16.1, 19.1, 19.4], False),
    "BBW": ([12.5, 13.5, 13.2, 12.3, -7.9, 0.5], False),          # COVID: hundimiento, no pico
    "PRDO": ([23.2, 25.6, 21.2, 18.6, 21.5, 20.8, 13.8, 12.3, 5.7, -4.6], False),  # recuperación
    "NUE": ([7.9, 9.4, 18.1, 24.7, 25.2, 4.1, 7.9, 12.9, 8.6, 8.0], True),
    "MOS": ([6.8, 5.6, 9.8, 25.0, 20.0, 4.8, -12.3, 9.7, 6.3, 4.5], True),
    "CALM": ([12.0, 36.1, 13.4, 30.8, 8.1, -1.9, 0.1, 3.4, 6.8, -12.3], True),
}


@pytest.mark.parametrize("ticker", list(_REAL_PROFILES))
def test_cyclicality_detector_on_real_margin_profiles(ticker):
    margins, expected = _REAL_PROFILES[ticker]
    a = assess_cyclicality(_margins(margins))
    assert a.is_cyclical is expected, a.evidence


def test_cyclicality_reports_peak_and_median():
    a = assess_cyclicality(_margins(_REAL_PROFILES["CALM"][0], last_year=2026))
    assert a.peak_margin == pytest.approx(0.361) and a.peak_period == "FY2025"
    assert a.median_margin == pytest.approx((6.8 + 8.1) / 200)       # 10 valores → media de los 2 centrales
    assert a.years == 10 and "36.1%" in a.evidence


def test_cyclicality_needs_five_years():
    a = assess_cyclicality(_margins([36.0, 5.0, 4.0, 6.0]))
    assert a.is_cyclical is False and "insufficient" in a.evidence


@pytest.mark.parametrize("ticker,expected", [("aapl", False), ("bbw", False), ("azo", False),
                                             ("prdo", False), ("calm", True)])
def test_archetypes_cyclicality(ticker, expected):
    fin = EdgarAdapter.parse_financials(json.loads((FIXTURES / f"{ticker}_xbrl_raw.json").read_text()))
    assert _cyclicality(fin).is_cyclical is expected


# --- Valoración con una cíclica sintética (números calculables a mano) ---
def _mv(v, p="FY2025", unit="USD"):
    return MetricValue(value=v, unit=unit, period=p, source="test")


def _series(values, unit="USD"):
    return [_mv(v, f"FY{2025 - i}", unit) for i, v in enumerate(values)]


def _cyclical_financials() -> FinancialsReport:
    # Ventas planas de 1 000; margen operativo 36/8/7/31/2/9 % → mediana 8,5 %, pico 36 % → cíclica.
    # OCF 300/60/50/250/20/70 sin capex → margen FCFF 30/6/5/25/2/7 % → mediana (6+7)/2 = 6,5 %.
    return FinancialsReport(
        ticker="CYC", cik="0000000002", as_of_date="2025-12-31", source_filing="10-K FY2025",
        revenue=_series([1000.0] * 6), gross_profit=_series([400.0] * 6),
        operating_income=_series([360.0, 80.0, 70.0, 310.0, 20.0, 90.0]),
        net_income=_series([270.0, 60.0, 52.0, 232.0, 15.0, 67.0]),
        operating_cash_flow=_series([300.0, 60.0, 50.0, 250.0, 20.0, 70.0]),
        capex=_series([0.0] * 6), free_cash_flow=_series([300.0, 60.0, 50.0, 250.0, 20.0, 70.0]),
        interest_expense=_series([0.0] * 6), interest_income=_series([0.0] * 6), sbc=_series([0.0] * 6),
        total_assets=_mv(2000.0), total_debt=_mv(0.0), cash_and_equivalents=_mv(0.0),
        shares_outstanding=_mv(10.0, unit="shares"),
    )


def _cyclical_state(historical_cagr_5y=0.166):
    return {
        "ticker": "CYC", "financials": _cyclical_financials(),
        "historical_cagr_5y": historical_cagr_5y,
        "market": MarketReport(ticker="CYC", stub=False,
                               price_current=MetricValue(value=60.0, unit="USD", period="2026-01-01", source="t"),
                               market_cap=MetricValue(value=600.0, unit="USD", period="2026-01-01", source="t")),
        "checkpoint2_user_input": {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10,
                                   "mid_year_convention": False, "valuation_date": "2025-12-31"},
    }


def test_build_fcff_uses_mid_cycle_margin_for_cyclicals():
    _, base, _, _, source = _build_fcff(_cyclical_financials(), 0.25, cyclical=True)
    assert base == pytest.approx(65.0)                      # 6,5 % × 1 000 (no la mediana de 3 años, 60)
    assert "mid-cycle" in source


def test_near_term_growth_is_terminal_for_cyclicals_unless_user_overrides():
    fin = _cyclical_financials()
    assert _compute_near_term_growth(fin, 0.166, None, terminal_growth=0.025) == (0.025, "cyclical_terminal")
    assert _compute_near_term_growth(fin, 0.166, 8.0, terminal_growth=0.025) == (0.08, "user_override")


def test_valuation_agent_normalizes_cyclical_end_to_end():
    r = valuation_agent(_cyclical_state())["valuation"]
    assert r.cyclicality is not None and r.cyclicality.is_cyclical is True
    assert r.near_term_growth_source == "cyclical_terminal"
    assert r.fcff_base.value == pytest.approx(65.0)
    # Valor terminal desde el EBIT mid-cycle: 8,5 % × 1 000 × (1 − 25 %) = 63,75
    expected = dcf_components(65.0, 0.025, 0.025, 0.09, stage1_years=5, fade_years=5, nopat_base=63.75)
    assert r.enterprise_value_dcf.value == pytest.approx(expected.enterprise_value, abs=1)
    # EPV con EBIT mid-cycle: 85 × 0,75 / 0,09 = 708,33 (la mediana de 5 años en dólares daría 666,67)
    assert r.epv_enterprise.value == pytest.approx(708, abs=1)
    assert r.epv_per_share.value == pytest.approx(70.83, abs=0.01)
    assert "Cyclical" in r.dcf_reliability_note


def test_reverse_dcf_compares_cyclicals_against_full_window_revenue_cagr():
    # Ventas planas 6 años → CAGR de toda la ventana 0,0 %, no el 16,6 % de historical_cagr_5y.
    r = valuation_agent(_cyclical_state())["valuation"]
    assert "historical 0.0%" in r.valuation_methods_summary[2]["display_upside"]


def test_non_cyclical_unchanged():
    from tests.test_valuation import _levered_state
    r = valuation_agent(_levered_state())["valuation"]
    assert r.cyclicality is None or r.cyclicality.is_cyclical is False
    assert r.fcff_base.value == pytest.approx(210e6)


# --- Nota contradictoria del tope del 30 % (afecta a todas las empresas) ---
def test_cap_note_absent_when_revenue_cap_applied():
    fin = FinancialsReport(
        ticker="FST", cik="0000000003", as_of_date="2025-12-31", source_filing="10-K FY2025",
        revenue=_series([1000.0, 950.0, 900.0, 850.0, 800.0]),
        gross_profit=_series([400.0] * 5),
        operating_income=_series([100.0, 95.0, 90.0, 85.0, 80.0]),       # 10 % estable → no cíclica
        net_income=_series([75.0, 71.0, 67.0, 64.0, 60.0]),
        operating_cash_flow=_series([200.0, 50.0]), capex=_series([0.0, 0.0]),
        free_cash_flow=_series([200.0, 50.0]),                            # CAGR 300 % → tope por ventas
        total_assets=_mv(2000.0), total_debt=_mv(0.0), cash_and_equivalents=_mv(0.0),
        shares_outstanding=_mv(10.0, unit="shares"),
    )
    st = _cyclical_state(historical_cagr_5y=0.05)
    st["financials"] = fin
    r = valuation_agent(st)["valuation"]
    # ADR-043: g1 = tendencia de ventas por mínimos cuadrados (1000/950/900/850/800 → ≈ 5,7 %).
    from due_diligence.utils.periods import least_squares_growth
    assert r.near_term_growth_source == "revenue_trend_4y"
    assert r.near_term_growth_used_pct == pytest.approx(least_squares_growth(fin.revenue)[0], abs=1e-4)
    assert "capped at 30%" not in r.dcf_reliability_note


# --- Consumidores: el informe y el Value Expert lo ven ---
def test_report_and_expert_context_show_cyclicality():
    st = _cyclical_state()
    st["valuation"] = valuation_agent(st)["valuation"]
    md = _render_markdown(st)
    assert "Cyclical: yes" in md and "36.0%" in md
    ctx = _format_context(st)
    assert "## CYCLICALITY (deterministic)" in ctx and "mid-cycle" in ctx


def test_checkpoint2_context_flags_cyclicality():
    # El usuario decide g1 en el checkpoint 2: debe ver que es cíclica antes de mirar el CAGR.
    from due_diligence.agents.checkpoint import _build_checkpoint2_summary
    ctx = _build_checkpoint2_summary(_cyclical_state())["context"]
    assert ctx["cyclicality"].startswith("Cyclical: yes")


def test_expert_historical_cagr_block_is_qualified_for_cyclicals():
    # En el run de CALM el Expert citó el CAGR de 5 años (16,6 %) mientras la tabla usaba el de
    # toda la ventana: el bloque de contexto histórico debe avisar de que en una cíclica no es ancla.
    st = _cyclical_state()
    st["valuation"] = valuation_agent(st)["valuation"]
    ctx = _format_context(st)
    assert "CYCLICAL: this CAGR depends on the cycle endpoints" in ctx
