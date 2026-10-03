"""
Tests para EPV (Earnings Power Value) — Greenwald, ADR-029.

Cubre _ebit_median, _compute_epv y la integración con net_debt / shares.
"""
import pytest
from due_diligence.agents.valuation import _ebit_median, _compute_epv
from due_diligence.schemas.base import MetricValue


def _mv(value: float, period: str = "FY2024") -> MetricValue:
    return MetricValue(value=value, unit="USD", period=period, source="test")


# --- _ebit_median ---

def test_ebit_median_5y_returns_correct_median():
    # valores: 100, 120, 110, 130, 115 → sorted: 100, 110, 115, 120, 130 → mediana = 115
    series = [_mv(v) for v in [100, 120, 110, 130, 115]]
    med, source = _ebit_median(series)
    assert med == pytest.approx(115.0)
    assert source == "ebit_median_5y"


def test_ebit_median_3y():
    series = [_mv(v) for v in [200, 180, 190]]
    med, source = _ebit_median(series)
    assert med == pytest.approx(190.0)
    assert "3y" in source


def test_ebit_median_falls_back_to_latest_when_only_1_period():
    series = [_mv(300.0)]
    med, source = _ebit_median(series)
    assert med == pytest.approx(300.0)
    assert source == "ebit_latest"


def test_ebit_median_empty_series():
    med, source = _ebit_median([])
    assert med is None
    assert source == "unavailable"


def test_ebit_median_includes_non_positive_years():
    # Revisión adversarial: el trough cíclico forma parte del ciclo → se INCLUYE.
    # 4 valores: sorted −50, 100, 110, 120 → mediana (100+110)/2 = 105 (antes descartaba −50 → 110)
    series = [_mv(-50), _mv(100), _mv(120), _mv(110)]
    med, source = _ebit_median(series)
    assert med == pytest.approx(105.0)
    assert source == "ebit_median_4y"


# --- _compute_epv ---

def test_compute_epv_enterprise_value():
    """EPV_enterprise = NOPAT / WACC con tax derivada de NI/EBIT."""
    # EBIT ~190M (mediana de 3 periodos), NI=150M → tax=(190-150)/190≈21%
    # NOPAT = 190M × (1-0.21) ≈ 150M → EPV = 150M / 0.09 ≈ 1.67B
    series = [_mv(190e6), _mv(200e6), _mv(180e6)]
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=series,
        net_income_latest=150e6,
        wacc=0.09,
        net_debt=0.0,
        shares=10e6,
        market_cap=1.5e9,
    )
    assert epv_ent is not None
    assert 1.5e9 < epv_ent.value < 2.0e9
    assert source is not None


def test_compute_epv_per_share_subtracts_net_debt():
    """EPV per share debe restar net_debt antes de dividir."""
    # EBIT=180M (solo 1 período → ebit_latest), tax=25% (default)
    # NOPAT = 180M × 0.75 = 135M → EPV_enterprise = 135M / 0.09 = 1.5B
    # EPV_equity = 1.5B - 200M = 1.3B → per_share = 1.3B / 10M = $130
    series = [_mv(180e6)]
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=series,
        net_income_latest=None,  # fuerza tax default 25%
        wacc=0.09,
        net_debt=200e6,
        shares=10e6,
        market_cap=1e9,
    )
    assert epv_ent is not None
    assert epv_ps is not None
    assert abs(epv_ps.value - 130.0) < 2.0  # tolerancia por redondeo


def test_compute_epv_upside_positive_when_epv_above_market():
    """Si EPV_enterprise > market_cap, upside debe ser positivo."""
    series = [_mv(100e6), _mv(120e6), _mv(110e6)]
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=series,
        net_income_latest=None,
        wacc=0.09,
        net_debt=0.0,
        shares=5e6,
        market_cap=500e6,   # EPV ≈ 110M×0.75/0.09 ≈ 917M > 500M
    )
    assert upside is not None
    assert upside > 0


def test_compute_epv_returns_none_when_no_data():
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=[],
        net_income_latest=None,
        wacc=0.09,
        net_debt=0.0,
        shares=None,
        market_cap=None,
    )
    assert epv_ent is None
    assert epv_ps is None
    assert upside is None


def test_compute_epv_handles_negative_ebit():
    """EBIT negativos se incluyen en la mediana; mediana ≤ 0 → EPV None (no un EPV negativo)."""
    # 2 periodos (< 3) → "ebit_latest" = −100M → None
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=[_mv(-100e6), _mv(50e6)],
        net_income_latest=None, wacc=0.09, net_debt=0.0, shares=1e6, market_cap=100e6,
    )
    assert epv_ent is None and epv_ps is None and upside is None
    # 3 periodos 100, −50, 80 → mediana 80M (con el −50 incluido) → EPV = 80 × 0.75 / 0.09 = 666.67M
    epv_ent, _, _, source = _compute_epv(
        ebit_series=[_mv(100e6), _mv(-50e6), _mv(80e6)],
        net_income_latest=None, wacc=0.09, net_debt=0.0, shares=1e6, market_cap=100e6,
    )
    assert epv_ent.value == pytest.approx(80e6 * 0.75 / 0.09, abs=1)
    assert source == "ebit_median_3y"
