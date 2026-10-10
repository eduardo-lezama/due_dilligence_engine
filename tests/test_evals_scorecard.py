"""
Tests del scorecard y la puerta (H2-1 Task 4): agregación POR CASO, desacuerdo por pares, puerta
pareada caso a caso y repuntuación sin LLM.
"""
import pytest

from due_diligence.evals.runner import EvalRun, RunRecord
from due_diligence.evals.scorecard import (CaseResult, GateNoise, Scorecard, compare, disagreement, load_run,
                                           save_run, score)

_SECTIONS = ("business_understanding", "moat_analysis", "management_assessment", "financial_quality",
             "growth_reinvestment", "valuation_section", "risks", "psychological_lens", "decision_frame")
_NOISE = GateNoise(max_case_flips=1, disagreement_delta=0.1, ungrounded_delta=0.5)
CONV = "## VALUATION METHODS CONVERGENCE (use in §6 — read Rule #21)\nConvergence: 1 cheap, 0 fair, 1 expensive out of 4 methods.\n"


def _raw(verdict="HOLD", valuation="2 of 4 methods signal cheap; long enough section text here."):
    d = {s: f"{s} text long enough to be a real section of the analysis." for s in _SECTIONS}
    d.update(valuation_section=valuation, verdict=verdict, verdict_confidence=0.6, suggested_sizing_pct=2.0,
             verdict_rationale=f"{verdict} for now.")
    return d


def _rec(case, i, verdict="HOLD", error=None, transport=False, **raw_over):
    raw = None if error else {**_raw(verdict), **raw_over}
    report = {**(raw or {}), "ticker": case, "stub": bool(error), "error": error}
    return RunRecord(case=case, repeat=i, raw=raw, report=report, context_sha="x", error=error,
                     transport_error=transport, latency_s=1.0)


def _run(records, cases, repeat=3, contexts=None):
    return EvalRun(records=records, cases=cases, repeat=repeat, contexts=contexts or {c: CONV for c in cases},
                   expert_model_requested="m", calls_by_model={"m": len(records)}, cost_usd=0.1, prompt_sha="p" * 12,
                   manifest_frozen_with={"git_sha": "a"})


def _states(cases):
    return {c: {"ticker": c} for c in cases}


def test_pairwise_disagreement_is_unbiased_for_small_n():
    assert disagreement({"HOLD": 3, "BUY": 2}) == pytest.approx(1 - (3 * 2 + 2 * 1) / (5 * 4))   # 0.6
    assert disagreement({"HOLD": 5}) == 0.0
    assert disagreement({}) == 0.0


def test_score_aggregates_by_case_with_majority():
    recs = [_rec("A", 0), _rec("A", 1), _rec("A", 2, valuation_section="cheap, no count here, long enough text section.")]
    sc = score(_run(recs, ["A"]), _states(["A"]), label="t")
    [cr] = sc.case_results
    assert cr.checks["rule21_count"] is True                 # 2 de 3 repeticiones pasan → mayoría
    assert cr.checks["buy_floor"] is None                    # nunca aplica
    assert cr.verdicts == {"HOLD": 3} and cr.disagreement == 0.0 and cr.n_ok_runs == 3


def test_score_counts_errors_and_ignores_them_for_stability():
    recs = [_rec("A", 0), _rec("A", 1, error="LLMTruncatedError: x"), _rec("A", 2, error="LLMProviderError: 429",
                                                                          transport=True)]
    sc = score(_run(recs, ["A"]), _states(["A"]), label="t")
    assert sc.n_errors_non_transport == 1 and sc.n_errors_transport == 1 and sc.case_results[0].n_ok_runs == 1


def _sc(*, cases=("A", "B", "C", "D"), check=None, disagree=0.0, ungrounded=0.0, holds=0, errors=0,
        repeat=3, frozen="a"):
    check = check or {}
    results = [CaseResult(case=c, n_ok_runs=repeat, verdicts={"HOLD": repeat}, disagreement=disagree,
                          checks={"rule21_count": check.get(c, True)}, mean_ungrounded=ungrounded, mean_repairs=0.0)
               for c in cases]
    return Scorecard(label="x", cases=list(cases), repeat=repeat, quick=False, prompt_sha="p", expert_model_requested="m",
                     calls_by_model={}, manifest_frozen_with={"git_sha": frozen}, cost_usd=0.0, case_results=results,
                     hold_cases=holds, mean_disagreement=disagree, mean_ungrounded=ungrounded,
                     n_errors_non_transport=errors, n_errors_transport=0)


def test_gate_passes_identical():
    assert compare(_sc(), _sc(), noise=_NOISE).passed


def test_gate_tolerates_one_noisy_case_but_not_two():
    assert compare(_sc(), _sc(check={"A": False}), noise=_NOISE).passed
    g = compare(_sc(), _sc(check={"A": False, "B": False}), noise=_NOISE)
    assert not g.passed and any("rule21_count" in r for r in g.reasons)


def test_gate_counts_improvements_against_regressions():
    base = _sc(check={"C": False, "D": False})
    cand = _sc(check={"A": False, "B": False})               # 2 empeoran, 2 mejoran → neto 0
    assert compare(base, cand, noise=_NOISE).passed


def test_low_coverage_checks_are_report_only():
    g = compare(_sc(cases=("A", "B")), _sc(cases=("A", "B"), check={"A": False, "B": False}), noise=_NOISE)
    assert g.passed and "rule21_count" in g.report_only


def test_gate_rejects_mismatched_runs():
    g = compare(_sc(), _sc(cases=("A", "B", "C")), noise=_NOISE)
    assert not g.passed and "different case set" in g.reasons[0]
    assert not compare(_sc(), _sc(repeat=5), noise=_NOISE).passed
    g2 = compare(_sc(), _sc(frozen="b"), noise=_NOISE)
    assert not g2.passed and "different dataset" in g2.reasons[0]


def test_gate_on_stability_ungrounded_holds_and_errors():
    assert not compare(_sc(disagree=0.0), _sc(disagree=0.2), noise=_NOISE).passed
    assert not compare(_sc(ungrounded=1.0), _sc(ungrounded=2.0), noise=_NOISE).passed
    assert not compare(_sc(holds=1), _sc(holds=3), noise=_NOISE).passed
    assert compare(_sc(errors=0), _sc(errors=1), noise=_NOISE).passed       # +1 tolerado
    assert not compare(_sc(errors=0), _sc(errors=2), noise=_NOISE).passed


def test_run_roundtrip_and_rescore_without_llm(tmp_path):
    recs = [_rec("A", 0), _rec("A", 1)]
    run = _run(recs, ["A"], repeat=2)
    save_run(run, tmp_path / "r.json")
    loaded = load_run(tmp_path / "r.json")
    assert loaded == run
    assert score(loaded, _states(["A"]), label="t") == score(run, _states(["A"]), label="t")


def test_hard_rule_checks_block_on_any_regression_even_with_low_coverage():
    """Revisión final: con < 4 casos aplicables, buy_floor/divergencia/regla 24 eran solo informativos."""
    def sc(ok):
        s = _sc(cases=("A", "B"))
        for cr in s.case_results:
            cr.checks["rule21_divergence_no_buy"] = ok if cr.case == "A" else None
        return s
    g = compare(sc(True), sc(False), noise=_NOISE)
    assert not g.passed and any("rule21_divergence_no_buy" in r for r in g.reasons)


def test_cases_without_enough_valid_runs_make_the_gate_inconclusive():
    base, cand = _sc(), _sc()
    cand.case_results[0].n_ok_runs = 1                       # 1 de 3 repeticiones válida
    g = compare(base, cand, noise=_NOISE)
    assert not g.passed and "inconclusive" in g.reasons[0]
