"""
Tests de verdict_policy (H2-2, ADR-041): la parte numérica del veredicto la calcula el código.
Aritmética a mano + estados reales congelados (sin LLM ni red).
"""
from pathlib import Path

import pytest

from due_diligence.agents import verdict_policy as vp
from due_diligence.evals.dataset import load_case_state, load_manifest

CASES = Path(__file__).parent / "evals" / "cases"


def _state(name):
    m = load_manifest(CASES / "manifest.json")
    return load_case_state(next(c for c in m.cases if c.name == name), CASES)


# --- tipo de negocio: el código solo endurece la clase que dice el modelo ---
def test_business_class_is_hardened_never_softened():
    assert vp.effective_business_class("quality_compounder", moat="wide", is_cyclical=False)[0] == "quality_compounder"
    assert vp.effective_business_class("quality_compounder", moat="none", is_cyclical=False)[0] == "average"
    assert vp.effective_business_class("quality_compounder", moat="wide", is_cyclical=True)[0] == "cyclical_or_low_quality"
    assert vp.effective_business_class("average", moat="wide", is_cyclical=False)[0] == "average"   # no se sube
    cls, note = vp.effective_business_class("quality_compounder", moat=None, is_cyclical=False)
    assert cls == "average" and "moat" in note


def test_required_mos_follows_checklist_9_1():
    assert vp.REQUIRED_MOS == {"quality_compounder": 0.30, "average": 0.50, "cyclical_or_low_quality": 0.60}


# --- ancla: DCF para compounders fiables, mínimo(DCF, EPV) para el resto ---
def test_anchor_compounder_uses_reliable_dcf():
    assert vp.intrinsic_anchor("quality_compounder", dcf_ps=120.0, epv_ps=75.0, dcf_reliable=True) == (120.0, "DCF (quality compounder)")


def test_anchor_falls_back_to_min_when_dcf_unreliable_or_not_compounder():
    v, src = vp.intrinsic_anchor("quality_compounder", dcf_ps=120.0, epv_ps=75.0, dcf_reliable=False)
    assert v == 75.0 and "min" in src and "unreliable" in src
    assert vp.intrinsic_anchor("average", dcf_ps=101.77, epv_ps=45.60, dcf_reliable=True)[0] == 45.60


def test_anchor_ignores_non_positive_and_missing_values():
    assert vp.intrinsic_anchor("average", dcf_ps=-5.0, epv_ps=30.0, dcf_reliable=True)[0] == 30.0
    assert vp.intrinsic_anchor("average", dcf_ps=None, epv_ps=None, dcf_reliable=True)[0] is None


def test_dcf_reliability_rules():
    assert vp.dcf_is_reliable(terminal_value_share=0.60, growth_source="fcf_cagr_5y")
    assert not vp.dcf_is_reliable(terminal_value_share=0.80, growth_source="fcf_cagr_5y")
    assert not vp.dcf_is_reliable(terminal_value_share=0.50, growth_source="default_7pct")


# --- entrada y margen real (ejemplo BBW del plan) ---
def test_entry_price_and_actual_mos_bbw_example():
    assert vp.entry_price(45.60, 0.50) == pytest.approx(22.80)
    assert vp.entry_price(45.60, 0.30) == pytest.approx(31.92)
    assert vp.actual_mos(45.60, 25.53) == pytest.approx(1 - 25.53 / 45.60)


# --- el código solo puede hacer el veredicto más prudente ---
def test_buy_without_margin_is_downgraded_to_watchlist():
    v, caveat = vp.reconcile_verdict("BUY", price=25.53, entry=22.80)
    assert v == "WATCHLIST" and "entry price" in caveat
    assert vp.reconcile_verdict("BUY", price=20.0, entry=22.80) == ("BUY", None)
    v2, c2 = vp.reconcile_verdict("BUY", price=25.0, entry=None)
    assert v2 == "WATCHLIST" and "no entry price" in c2
    for other in ("WATCHLIST", "PASS", "SELL"):
        assert vp.reconcile_verdict(other, price=1.0, entry=100.0) == (other, None)     # nunca se sube


# --- confianza compuesta: base visible + ajuste del LLM acotado ---
def test_composite_confidence_components():
    conf, parts = vp.composite_confidence("BUY", cheap=3, expensive=0, dcf_reliable=True, verified_citations=12,
                                          llm_adjustment=0.4)
    assert parts == {"base": 0.5, "methods_agreement": 0.15, "evidence": 0.05, "llm_adjustment": 0.1}
    assert conf == pytest.approx(0.8)
    conf2, parts2 = vp.composite_confidence("SELL", cheap=1, expensive=2, dcf_reliable=False, verified_citations=3,
                                            llm_adjustment=-0.05)
    assert parts2["methods_agreement"] == pytest.approx(0.05) and parts2["dcf_unreliable"] == -0.1
    assert conf2 == pytest.approx(0.5 + 0.05 - 0.1 - 0.05)
    conf3, parts3 = vp.composite_confidence("WATCHLIST", cheap=3, expensive=0, dcf_reliable=True,
                                            verified_citations=0, llm_adjustment=0.0)
    assert parts3["methods_agreement"] == 0.0 and conf3 == pytest.approx(0.5)


# --- tamaño por margen y riesgo, no por confianza ---
def test_position_size_rules():
    assert vp.position_size("WATCHLIST", actual_mos=0.6, required_mos=0.3, price=10.0, epv_ps=20.0) == (0.0, {"not_buy": 0.0})
    size, parts = vp.position_size("BUY", actual_mos=0.35, required_mos=0.30, price=10.0, epv_ps=20.0)
    assert size == 5.0 and parts == {"base": 5.0}
    size, parts = vp.position_size("BUY", actual_mos=0.50, required_mos=0.30, price=10.0, epv_ps=8.0)
    assert size == 5.0 and parts == {"base": 5.0, "deep_margin": 2.5, "price_above_epv": -2.5}


# --- decisión completa sobre estados reales ---
def test_decide_on_real_states():
    bbw = vp.decide("BUY", "quality_compounder", _state("BBW-base"), llm_adjustment=0.0, verified_citations=20)
    # BBW: moat none → clase 'average' (50 %), ancla min(DCF, EPV) = EPV 45,60 → entrada 22,80 < precio 25,53
    assert bbw.business_class == "average" and bbw.anchor_value == pytest.approx(45.60, abs=0.01)
    assert bbw.entry_price == pytest.approx(22.80, abs=0.01) and bbw.verdict == "WATCHLIST" and bbw.verdict_raw == "BUY"
    plug = vp.decide("SELL", "cyclical_or_low_quality", _state("PLUG-base"), llm_adjustment=0.0, verified_citations=5)
    assert plug.anchor_value is None and plug.entry_price is None and plug.verdict == "SELL"
    aapl = vp.decide("WATCHLIST", "quality_compounder", _state("AAPL-base"), llm_adjustment=0.0, verified_citations=30)
    assert aapl.business_class == "quality_compounder" and aapl.entry_price is not None
    assert aapl.sizing_pct == 0.0
