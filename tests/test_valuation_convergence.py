"""
Tests para convergence signaling — classifiers y _build_methods_summary (ADR-030).
"""
import pytest
from due_diligence.agents.valuation import (
    _classify_dcf_upside,
    _classify_epv_upside,
    _classify_reverse_vs_historical,
    _classify_ev_ebit_vs_peers,
    _build_methods_summary,
    _three_stage_dcf,
    _resolve_stages,
)
from due_diligence.schemas.base import MetricValue


def _mv(value: float, unit: str = "USD/share") -> MetricValue:
    return MetricValue(value=value, unit=unit, period="FY2024", source="test")


# --- _classify_dcf_upside ---

def test_dcf_cheap():
    assert _classify_dcf_upside(25.0) == "cheap"

def test_dcf_barely_cheap():
    assert _classify_dcf_upside(20.1) == "cheap"

def test_dcf_fair_positive():
    assert _classify_dcf_upside(10.0) == "fair"

def test_dcf_fair_zero():
    assert _classify_dcf_upside(0.0) == "fair"

def test_dcf_fair_negative_small():
    assert _classify_dcf_upside(-10.0) == "fair"

def test_dcf_expensive():
    assert _classify_dcf_upside(-25.0) == "expensive"

def test_dcf_none():
    assert _classify_dcf_upside(None) == "n/a"


# --- _classify_epv_upside ---

def test_epv_cheap():
    assert _classify_epv_upside(15.0) == "cheap"

def test_epv_fair_low_positive():
    # 0-10%: mercado paga ligeramente por encima del EPV (paga algo de growth)
    assert _classify_epv_upside(5.0) == "fair"

def test_epv_expensive():
    # EPV < market_cap: precio ya asume crecimiento
    assert _classify_epv_upside(-20.0) == "expensive"

def test_epv_border_zero():
    # En el límite: justo en 0 → fair (mercado paga igual que EPV)
    assert _classify_epv_upside(0.0) == "fair"

def test_epv_none():
    assert _classify_epv_upside(None) == "n/a"


# --- _classify_reverse_vs_historical ---

def test_reverse_cheap_market_pessimistic():
    # implied 3%, historical 8% → spread -5pp < -3pp → cheap
    assert _classify_reverse_vs_historical(0.03, 0.08) == "cheap"

def test_reverse_fair_small_spread():
    # implied 7%, historical 8% → spread -1pp, dentro del ±3pp → fair
    assert _classify_reverse_vs_historical(0.07, 0.08) == "fair"

def test_reverse_expensive_market_optimistic():
    # implied 15%, historical 8% → spread +7pp > +3pp → expensive
    assert _classify_reverse_vs_historical(0.15, 0.08) == "expensive"

def test_reverse_none_implied():
    assert _classify_reverse_vs_historical(None, 0.08) == "n/a"

def test_reverse_none_historical():
    assert _classify_reverse_vs_historical(0.05, None) == "n/a"


# --- _classify_ev_ebit_vs_peers ---

def test_ev_ebit_cheap():
    # 8x vs 15x peer median → ratio 0.53 < 0.70 → cheap
    assert _classify_ev_ebit_vs_peers(8.0, 15.0) == "cheap"

def test_ev_ebit_fair():
    # 14x vs 15x peer median → ratio 0.93, dentro de 0.70-1.30 → fair
    assert _classify_ev_ebit_vs_peers(14.0, 15.0) == "fair"

def test_ev_ebit_expensive():
    # 25x vs 15x peer median → ratio 1.67 > 1.30 → expensive
    assert _classify_ev_ebit_vs_peers(25.0, 15.0) == "expensive"

def test_ev_ebit_no_peer_data():
    assert _classify_ev_ebit_vs_peers(12.0, None) == "n/a"

def test_ev_ebit_no_subject_data():
    assert _classify_ev_ebit_vs_peers(None, 15.0) == "n/a"


# --- _build_methods_summary ---

def test_build_methods_summary_returns_4_rows():
    rows = _build_methods_summary(
        dcf_per_share=_mv(80.0), dcf_upside_pct=30.0,
        epv_per_share=_mv(60.0), epv_upside_pct=15.0,
        reverse_growth=0.04, historical_cagr=0.10,
        ev_ebit=_mv(12.0, "x"), peer_ev_ebit_median=18.0,
    )
    assert len(rows) == 4


def test_build_methods_summary_all_cheap():
    rows = _build_methods_summary(
        dcf_per_share=_mv(100.0), dcf_upside_pct=40.0,   # cheap
        epv_per_share=_mv(80.0), epv_upside_pct=25.0,    # cheap
        reverse_growth=0.02, historical_cagr=0.10,        # cheap (market pessimistic)
        ev_ebit=_mv(8.0, "x"), peer_ev_ebit_median=15.0, # cheap
    )
    classifications = [r["classification"] for r in rows]
    assert all(c == "cheap" for c in classifications)


def test_build_methods_summary_required_keys():
    rows = _build_methods_summary(
        dcf_per_share=None, dcf_upside_pct=None,
        epv_per_share=None, epv_upside_pct=None,
        reverse_growth=None, historical_cagr=None,
        ev_ebit=None, peer_ev_ebit_median=None,
    )
    assert len(rows) == 4
    for r in rows:
        assert "method" in r
        assert "classification" in r
        assert "display_upside" in r


def test_build_methods_summary_na_when_no_data():
    rows = _build_methods_summary(
        dcf_per_share=None, dcf_upside_pct=None,
        epv_per_share=None, epv_upside_pct=None,
        reverse_growth=None, historical_cagr=None,
        ev_ebit=None, peer_ev_ebit_median=None,
    )
    for r in rows:
        assert r["classification"] == "n/a"


# --- _three_stage_dcf y _resolve_stages ---

def test_three_stage_less_than_flat():
    """Fade debe producir valor menor que growth flat del mismo g1."""
    from due_diligence.agents.valuation import _two_stage_dcf
    flat = _two_stage_dcf(100e6, 0.10, 0.025, 0.09, 10)
    staged = _three_stage_dcf(100e6, g1=0.10, terminal_growth=0.025,
                               wacc=0.09, stage1_years=5, fade_years=5)
    assert staged < flat
    assert staged > 0


def test_three_stage_fade_zero_equals_two_stage():
    """fade_years=0 debe ser idéntico al 2-stage original."""
    from due_diligence.agents.valuation import _two_stage_dcf
    v1 = _two_stage_dcf(100e6, 0.10, 0.025, 0.09, 10)
    v2 = _three_stage_dcf(100e6, g1=0.10, terminal_growth=0.025,
                           wacc=0.09, stage1_years=10, fade_years=0)
    assert abs(v1 - v2) < 1.0  # diferencias de redondeo únicamente


def test_three_stage_all_zero_growth():
    """g1=0 sin fade debe ser EPV-like: base_fcf * (1+terminal) / (wacc-terminal)."""
    val = _three_stage_dcf(100e6, g1=0.0, terminal_growth=0.025,
                            wacc=0.09, stage1_years=1, fade_years=0)
    assert val > 0


def test_resolve_stages_auto_50_50():
    s1, fd = _resolve_stages(10, None, None)
    assert s1 == 5
    assert fd == 5
    assert s1 + fd == 10


def test_resolve_stages_5y():
    s1, fd = _resolve_stages(5, None, None)
    assert s1 >= 1
    assert s1 + fd == 5


def test_resolve_stages_explicit_both():
    s1, fd = _resolve_stages(10, 3, 4)
    assert s1 == 3
    assert fd == 4


def test_resolve_stages_explicit_stage1_only():
    s1, fd = _resolve_stages(10, 3, None)
    assert s1 == 3
    assert fd == 7
