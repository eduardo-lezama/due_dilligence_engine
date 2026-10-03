"""
Tests de utils/periods — alineación por año fiscal (EC-82) y CAGR por años reales (EC-83).

Caso real que motiva los tests: BBW no tiene FY2025 en la vista curada de edgartools,
así que el índice 3 de la serie son 4 años, no 3.
"""
import pytest

from due_diligence.schemas.base import MetricValue
from due_diligence.utils.periods import align_by_period, cagr_by_fiscal_years

mv = lambda v, p: MetricValue(value=v, unit="USD", period=p, source="XBRL")  # noqa: E731


def test_align_by_period_never_pairs_different_years():
    ocf = [mv(65.1, "FY2026"), mv(64.3, "FY2024")]
    capex = [mv(25.5, "FY2026"), mv(20.0, "FY2025"), mv(18.3, "FY2024")]
    pairs = align_by_period(ocf, capex)
    assert [(o.period, c.period) for o, c in pairs] == [("FY2026", "FY2026"), ("FY2024", "FY2024")]


def test_cagr_uses_fiscal_year_distance_not_index():
    # BBW real: falta FY2025 → índice 3 = 4 años, no 3
    rev = [mv(529.8, "FY2026"), mv(486.1, "FY2024"), mv(467.9, "FY2023"), mv(411.5, "FY2022")]
    cagr, n = cagr_by_fiscal_years(rev, max_years=5)
    assert n == 4
    assert cagr == pytest.approx((529.8 / 411.5) ** 0.25 - 1)


def test_cagr_window_caps_at_max_years():
    rev = [mv(200, "FY2025"), mv(150, "FY2021"), mv(100, "FY2018")]
    assert cagr_by_fiscal_years(rev, max_years=5)[1] == 4


def test_fcf_scan_skips_negative_start_with_year_distance():
    fcf = [mv(352, "FY2026"), mv(292, "FY2024"), mv(-50, "FY2023")]
    assert cagr_by_fiscal_years(fcf, scan_for_positive_start=True)[1] == 2


def test_cagr_without_scan_returns_none_on_negative_start():
    """Revenue (sin scan): si el punto más antiguo de la ventana es ≤ 0 → None (semántica previa)."""
    rev = [mv(352, "FY2026"), mv(292, "FY2024"), mv(-50, "FY2023")]
    assert cagr_by_fiscal_years(rev, max_years=5) is None


def test_cagr_positional_fallback_for_non_fy_labels():
    """Etiquetas no anuales (datos sintéticos legacy) → fallback posicional, sin romper."""
    s = [mv(121.0, "2025"), mv(110.0, "2024"), mv(100.0, "2023")]
    cagr, n = cagr_by_fiscal_years(s)
    assert n == 2 and cagr == pytest.approx(0.1)
