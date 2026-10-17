"""
Tests de verdict_policy (H2-2 ADR-041; bloque B ADR-044): la parte numérica del veredicto la calcula el código.

Desde el bloque B el precio de entrada y la rentabilidad implícita los calcula la valoración (tool lane);
aquí se comprueba que la política los aplica bien: solo hace el veredicto más prudente, dimensiona por el
caso pesimista y dice lo que no pudo comprobar. Estados sintéticos (los congelados de evals son anteriores
al bloque B y se re-congelan en B4).
"""
import pytest

from due_diligence.agents import verdict_policy as vp
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import (CompetitiveReport, CyclicalityAssessment, DcfScenario, MarketReport,
                                           ValuationReport)


def _mv(v, unit="USD"):
    return MetricValue(value=v, unit=unit, period="FY2025", source="test")


def _state(*, price=25.0, entry=30.0, implied=0.12, bear=0.06, hurdle=0.10, epv=20.0, moat="narrow",
           cyclical=False, assessed=True, basis="base case returns the 10.0% hurdle"):
    scenarios = [DcfScenario(name="bear", fcff_margin=0.08, near_term_growth=0.02, per_share=20.0, implied_return=bear),
                 DcfScenario(name="base", fcff_margin=0.10, near_term_growth=0.05, per_share=35.0, implied_return=implied)]
    cyc = CyclicalityAssessment(is_cyclical=cyclical, years=10 if assessed else 3, median_margin=0.1,
                                peak_margin=0.12, peak_period="FY2024", evidence="test", assessed=assessed)
    val = ValuationReport(ticker="T", stub=False, entry_price=entry, implied_return=implied, hurdle_rate=hurdle,
                          entry_basis=basis, dcf_scenarios=scenarios, epv_per_share=_mv(epv) if epv else None,
                          cyclicality=cyc, terminal_value_share=0.5, near_term_growth_source="revenue_trend_10y",
                          valuation_methods_summary=[{"classification": "cheap"}, {"classification": "fair"}])
    return {"valuation": val, "market": MarketReport(ticker="T", stub=False, price_current=_mv(price)),
            "competitive": CompetitiveReport(ticker="T", stub=False, moat_classification=moat)}


# --- tipo de negocio: el código solo endurece la clase que dice el modelo (descriptiva desde ADR-044) ---
def test_business_class_is_hardened_never_softened():
    assert vp.effective_business_class("quality_compounder", moat="wide", is_cyclical=False)[0] == "quality_compounder"
    assert vp.effective_business_class("quality_compounder", moat="none", is_cyclical=False)[0] == "average"
    assert vp.effective_business_class("quality_compounder", moat="wide", is_cyclical=True)[0] == "cyclical_or_low_quality"
    assert vp.effective_business_class("average", moat="wide", is_cyclical=False)[0] == "average"   # no se sube
    cls, note = vp.effective_business_class("quality_compounder", moat=None, is_cyclical=False)
    assert cls == "average" and "moat" in note


def test_dcf_reliability_rules():
    assert vp.dcf_is_reliable(terminal_value_share=0.60, growth_source="revenue_trend_10y")
    assert not vp.dcf_is_reliable(terminal_value_share=0.80, growth_source="revenue_trend_10y")
    assert not vp.dcf_is_reliable(terminal_value_share=0.50, growth_source="default_7pct")


# --- el código solo puede hacer el veredicto más prudente ---
def test_buy_above_entry_is_downgraded_to_watchlist():
    v, caveat = vp.reconcile_verdict("BUY", price=25.53, entry=22.80)
    assert v == "WATCHLIST" and "entry price" in caveat and "hurdle" in caveat
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


# --- tamaño por riesgo de pérdida (caso pesimista), no por confianza ---
def test_position_size_rules():
    assert vp.position_size("WATCHLIST", bear_return=0.2, hurdle=0.1, price=10.0, epv_ps=20.0) == (0.0, {"not_buy": 0.0})
    size, parts = vp.position_size("BUY", bear_return=0.06, hurdle=0.10, price=10.0, epv_ps=20.0)
    assert size == 5.0 and parts == {"base": 5.0}
    size, parts = vp.position_size("BUY", bear_return=0.12, hurdle=0.10, price=10.0, epv_ps=8.0)
    assert size == 5.0 and parts == {"base": 5.0, "bear_case_meets_hurdle": 2.5, "price_above_epv": -2.5}
    assert vp.position_size("BUY", bear_return=0.12, hurdle=0.10, price=10.0, epv_ps=12.0)[0] == 7.5


# --- decisión completa ---
def test_buy_below_entry_stays_buy_and_carries_the_return_figures():
    d = vp.decide("BUY", "quality_compounder", _state(), llm_adjustment=0.0, verified_citations=20)
    assert d.verdict == "BUY" and d.business_class == "quality_compounder"
    assert d.entry_price == 30.0 and d.implied_return == 0.12 and d.implied_return_bear == 0.06 and d.hurdle_rate == 0.10
    # Precio 25 > EPV 20 (paga crecimiento) y pesimista 6 % < listón: 5 − 2,5 = 2,5 %.
    assert d.sizing_pct == 2.5 and d.sizing_components == {"base": 5.0, "price_above_epv": -2.5}


def test_buy_above_entry_is_downgraded_and_class_hardened():
    d = vp.decide("BUY", "quality_compounder", _state(price=35.0, moat="none"), llm_adjustment=0.0,
                  verified_citations=20)
    assert d.verdict == "WATCHLIST" and d.verdict_raw == "BUY" and d.sizing_pct == 0.0
    assert d.business_class == "average" and any("hardened" in c for c in d.caveats)


def test_missing_valuation_means_no_entry_and_no_buy():
    d = vp.decide("BUY", "average", {"market": MarketReport(ticker="T", stub=False, price_current=_mv(10.0))},
                  llm_adjustment=0.0, verified_citations=0)
    assert d.verdict == "WATCHLIST" and d.entry_price is None and d.entry_basis == "no valuation"


def test_caveats_say_what_could_not_be_tested():
    d = vp.decide("WATCHLIST", "average", _state(assessed=False, basis="base case returns the 10.0% hurdle "
                                                 "(no bear scenario: downside not tested)"),
                  llm_adjustment=0.0, verified_citations=0)
    assert any("Cyclicality not assessed" in c for c in d.caveats)
    assert any("No bear scenario" in c for c in d.caveats)
