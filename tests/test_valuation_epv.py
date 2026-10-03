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



# --- _compute_epv (T12b: mismo bridge que el DCF, tipo MARGINAL, upside solo por acción) ---

def test_compute_epv_enterprise_value():
    """EPV_enterprise = NOPAT / WACC con el tipo MARGINAL 25 % (ADR-033)."""
    # Mediana de 190, 200, 180 = 190M → NOPAT = 190 × 0.75 = 142.5M → EPV = 142.5 / 0.09 = 1 583.33M
    series = [_mv(190e6), _mv(200e6), _mv(180e6)]
    epv_ent, epv_ps, upside, source, _ = _compute_epv(
        ebit_series=series, wacc=0.09, net_claims=0.0, shares=10e6,
    )
    assert epv_ent.value == pytest.approx(142.5e6 / 0.09, abs=1)
    assert source == "ebit_median_3y"


def test_compute_epv_per_share_subtracts_net_claims():
    """EPV per share resta net_claims (mismo bridge que el DCF) antes de dividir."""
    # EBIT=180M (1 periodo → ebit_latest): NOPAT 135M → EPV 1.5B − 200M = 1.3B / 10M = $130
    epv_ent, epv_ps, upside, source, _ = _compute_epv(
        ebit_series=[_mv(180e6)], wacc=0.09, net_claims=200e6, shares=10e6,
    )
    assert epv_ps.value == pytest.approx(130.0)


def test_compute_epv_upside_vs_price():
    """Upside = (EPV/sh − precio)/precio. Mediana 110M → 82.5/0.09 = 916.67M / 5M = $183.33;
    con precio 100 → +83.3 %."""
    series = [_mv(100e6), _mv(120e6), _mv(110e6)]
    _, epv_ps, upside, _, premium = _compute_epv(
        ebit_series=series, wacc=0.09, net_claims=0.0, shares=5e6, price_current=100.0,
    )
    assert epv_ps.value == pytest.approx(183.33, abs=0.01)
    assert upside == pytest.approx(83.3, abs=0.05)
    assert premium == pytest.approx(1 - 183.33 / 100, abs=1e-4)


def test_compute_epv_mid_year_and_capitalization_factors():
    # 135M/0.09 = 1.5B × 1.09^0.5 (mid-year) × 1.05 (capitalización) = 1.5B × 1.044031 × 1.05
    epv_ent, *_ = _compute_epv([_mv(180e6)], 0.09, net_claims=0.0, shares=10e6,
                               mid_year=True, capitalization=1.05)
    assert epv_ent.value == pytest.approx(1.5e9 * 1.09 ** 0.5 * 1.05, abs=1)


def test_compute_epv_returns_none_when_no_data():
    epv_ent, epv_ps, upside, source, premium = _compute_epv(
        ebit_series=[], wacc=0.09, net_claims=0.0, shares=None,
    )
    assert epv_ent is None and epv_ps is None and upside is None and premium is None


def test_compute_epv_handles_negative_ebit():
    """EBIT negativos se incluyen en la mediana; mediana ≤ 0 → EPV None (no un EPV negativo)."""
    # 2 periodos (< 3) → "ebit_latest" = −100M → None
    epv_ent, epv_ps, upside, source, _ = _compute_epv(
        ebit_series=[_mv(-100e6), _mv(50e6)], wacc=0.09, net_claims=0.0, shares=1e6,
    )
    assert epv_ent is None and epv_ps is None and upside is None
    # 3 periodos 100, −50, 80 → mediana 80M (con el −50 incluido) → EPV = 80 × 0.75 / 0.09 = 666.67M
    epv_ent, _, _, source, _ = _compute_epv(
        ebit_series=[_mv(100e6), _mv(-50e6), _mv(80e6)], wacc=0.09, net_claims=0.0, shares=1e6,
    )
    assert epv_ent.value == pytest.approx(80e6 * 0.75 / 0.09, abs=1)
    assert source == "ebit_median_3y"
