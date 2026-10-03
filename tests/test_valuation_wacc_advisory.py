"""
Tests para el WACC sugerido (ADR-029 → ADR-033).

- `_compute_suggested_wacc`: devuelve el COSTE DE EQUITY (CAPM; nombre histórico). La prima de
  tamaño es visible en el desglose pero solo se aplica con `size_premium=True` (Damodaran la
  rechaza y con β por industria en H3 se contaría dos veces).
- `suggest_wacc`: WACC real = wE·Ke + wD·Kd·(1−t_marginal) con Kd por rating sintético.
"""
import pytest

from due_diligence.agents.valuation import _compute_suggested_wacc, suggest_wacc
from due_diligence.schemas.base import FinancialsReport, MetricValue


def test_large_cap_no_size_premium():
    # >$10B → size_premium=0 → Ke = 4.2 + 1.0*5.0 + 0.0 = 9.2
    wacc, breakdown = _compute_suggested_wacc(market_cap=50e9)
    assert abs(wacc - 9.2) < 0.1
    assert "large cap" in breakdown.lower()
    assert "0.0%" in breakdown


def test_size_premium_off_by_default_but_visible():
    # $300M-$2B → prima 1.5 visible en el desglose, NO aplicada: Ke = 9.2
    wacc, breakdown = _compute_suggested_wacc(market_cap=800e6)
    assert wacc == pytest.approx(9.2)
    assert "small cap" in breakdown.lower() and "OFF" in breakdown


def test_mid_cap_adds_half_point():
    # $2B-$10B → size_premium=0.5 → Ke = 9.7 (solo con size_premium=True)
    wacc, breakdown = _compute_suggested_wacc(market_cap=5e9, size_premium=True)
    assert abs(wacc - 9.7) < 0.1
    assert "mid cap" in breakdown.lower()


def test_small_cap_adds_one_and_half_points():
    # $300M-$2B → size_premium=1.5 → Ke = 10.7
    wacc, breakdown = _compute_suggested_wacc(market_cap=800e6, size_premium=True)
    assert abs(wacc - 10.7) < 0.1
    assert "small cap" in breakdown.lower()


def test_micro_cap_adds_three_points():
    # <$300M → size_premium=3.0 → Ke = 12.2
    wacc, breakdown = _compute_suggested_wacc(market_cap=100e6, size_premium=True)
    assert abs(wacc - 12.2) < 0.1
    assert "micro cap" in breakdown.lower()


def test_none_market_cap_defaults_to_large():
    # None → large cap bucket (conservative default — no penalizar por falta de datos)
    wacc, breakdown = _compute_suggested_wacc(market_cap=None)
    assert abs(wacc - 9.2) < 0.1
    assert "large cap" in breakdown.lower()


def test_breakdown_contains_rf_erp_beta():
    """El breakdown debe ser legible para el usuario."""
    _, breakdown = _compute_suggested_wacc(market_cap=1e9)
    assert "Rf=" in breakdown
    assert "ERP=" in breakdown
    assert "β=" in breakdown


def test_return_type():
    wacc, breakdown = _compute_suggested_wacc(market_cap=2e9)
    assert isinstance(wacc, float)
    assert isinstance(breakdown, str)


# --- WACC real (T11) ---

def _azo_like_financials() -> FinancialsReport:
    """AZO-like: equity contable negativo, deuda 9B, cobertura EBIT/intereses 9.0×."""
    mv = lambda v, p="FY2025", u="USD": MetricValue(value=v, unit=u, period=p, source="test")  # noqa: E731
    return FinancialsReport(
        ticker="AZO", cik="1", as_of_date="2025-08-30", source_filing="10-K FY2025",
        revenue=[mv(18.9e9)], gross_profit=[mv(10e9)], operating_income=[mv(3.825e9)], net_income=[mv(2.652e9)],
        operating_cash_flow=[mv(3.0e9)], capex=[mv(0.8e9)], free_cash_flow=[mv(2.2e9)],
        interest_expense=[mv(0.425e9)], income_tax=[mv(0.748e9)], pretax_income=[mv(3.4e9)],
        total_assets=mv(19.355e9), total_debt=mv(9.0e9), cash_and_equivalents=mv(0.3e9),
        total_debt_series=[mv(9.0e9), mv(8.0e9, "FY2024")], stockholders_equity=mv(-3.4e9),
        shares_outstanding=mv(16.8e6, u="shares"),
    )


def test_suggest_wacc_azo_like():
    # Ke 9.2 % (large cap, prima de tamaño OFF). Kd: cobertura 3 825/425 = 9.0× → Rf 4.2 % + 0.6 % = 4.8 %.
    # Escudo fiscal al tipo MARGINAL 25 %: Kd_at = 3.6 %. Pesos: E 60/69, D 9/69 (deuda = total_debt_series[0]).
    # WACC = 0.869565×9.2 % + 0.130435×3.6 % = 8.0000 % + 0.4696 % = 8.4696 %
    w = suggest_wacc(_azo_like_financials(), market_cap=60e9)
    assert w.wacc == pytest.approx(0.084696, abs=1e-5)
    assert w.tax_rate == pytest.approx(0.25) and "coverage" in w.cost_of_debt_source
    assert w.equity_weight == pytest.approx(60 / 69)


def test_suggest_wacc_none_without_market_cap():
    assert suggest_wacc(_azo_like_financials(), market_cap=None) is None
    assert suggest_wacc(_azo_like_financials(), market_cap=0.0) is None
