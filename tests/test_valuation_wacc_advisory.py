"""
Tests para WACC advisory — _compute_suggested_wacc (ADR-029).
"""
import pytest
from due_diligence.agents.valuation import _compute_suggested_wacc


def test_large_cap_no_size_premium():
    # >$10B → size_premium=0 → WACC = 4.2 + 1.0*5.0 + 0.0 = 9.2
    wacc, breakdown = _compute_suggested_wacc(market_cap=50e9)
    assert abs(wacc - 9.2) < 0.1
    assert "large cap" in breakdown.lower()
    assert "0.0%" in breakdown


def test_mid_cap_adds_half_point():
    # $2B-$10B → size_premium=0.5 → WACC = 9.7
    wacc, breakdown = _compute_suggested_wacc(market_cap=5e9)
    assert abs(wacc - 9.7) < 0.1
    assert "mid cap" in breakdown.lower()


def test_small_cap_adds_one_and_half_points():
    # $300M-$2B → size_premium=1.5 → WACC = 10.7
    wacc, breakdown = _compute_suggested_wacc(market_cap=800e6)
    assert abs(wacc - 10.7) < 0.1
    assert "small cap" in breakdown.lower()


def test_micro_cap_adds_three_points():
    # <$300M → size_premium=3.0 → WACC = 12.2
    wacc, breakdown = _compute_suggested_wacc(market_cap=100e6)
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
