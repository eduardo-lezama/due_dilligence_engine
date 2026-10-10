"""
Tests del CLI del harness (scripts/run_evals.py) sin LLM: repuntuar, comparar con puerta y guardar
baseline sobre un run sintético construido con casos reales del manifiesto.
"""
import pytest

from due_diligence.evals.runner import EvalRun, RunRecord
from due_diligence.evals.scorecard import save_run
from scripts import run_evals

_SECTIONS = ("business_understanding", "moat_analysis", "management_assessment", "financial_quality",
             "growth_reinvestment", "valuation_section", "risks", "psychological_lens", "decision_frame")
_CASES = ["AAPL-base", "BBW-base", "AZO-base", "PRDO-base"]


def _run(verdict: str, sections_ok: bool = True) -> EvalRun:
    text = "long enough text for a real section of the analysis here." if sections_ok else "short"
    raw = {s: text for s in _SECTIONS}
    raw.update(verdict=verdict, verdict_confidence=0.6, suggested_sizing_pct=2.0, verdict_rationale=f"{verdict}.")
    recs = [RunRecord(case=c, repeat=i, raw=raw, report={**raw, "ticker": c, "stub": False}, context_sha="x",
                      latency_s=1.0) for c in _CASES for i in range(2)]
    return EvalRun(records=recs, cases=_CASES, repeat=2, contexts={c: "ctx" for c in _CASES},
                   expert_model_requested="m", calls_by_model={}, cost_usd=0.0, prompt_sha="p" * 12,
                   manifest_frozen_with={"git_sha": "a"})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(run_evals, "BASELINES_DIR", tmp_path / "baselines")
    monkeypatch.setattr(run_evals, "NOISE_FILE", tmp_path / "baselines" / "noise.json")
    return tmp_path


def test_rescore_and_gate_pass(isolated):
    save_run(_run("HOLD"), isolated / "base.run.json")
    save_run(_run("HOLD"), isolated / "cand.run.json")
    assert run_evals.main(["--rescore", str(isolated / "cand.run.json"),
                           "--compare", str(isolated / "base.run.json")]) == 0


def test_gate_failure_exits_1(isolated):
    save_run(_run("HOLD"), isolated / "base.run.json")
    save_run(_run("HOLD", sections_ok=False), isolated / "cand.run.json")     # 4 casos empeoran
    assert run_evals.main(["--rescore", str(isolated / "cand.run.json"),
                           "--compare", str(isolated / "base.run.json")]) == 1


def test_save_baseline_copies_run_and_scorecard_and_checks_kind(isolated):
    save_run(_run("HOLD"), isolated / "r.run.json")
    assert run_evals.main(["--rescore", str(isolated / "r.run.json"), "--save-baseline", "full"]) == 0
    assert (isolated / "baselines" / "full.run.json").exists()
    assert (isolated / "baselines" / "full.scorecard.json").exists()
    assert run_evals.main(["--rescore", str(isolated / "r.run.json"), "--save-baseline", "quick"]) == 1
