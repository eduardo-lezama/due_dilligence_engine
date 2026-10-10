"""
Tests del dataset de evals (H2-1 Task 1): manifiesto, carga de estados congelados con los schemas
actuales y la variante de hipótesis como transformación (no re-congelado).
"""
from pathlib import Path

import pytest

from due_diligence.evals.dataset import EvalCase, load_case_state, load_manifest, with_hypotheses

CASES_DIR = Path(__file__).parent / "evals" / "cases"
MANIFEST = load_manifest(CASES_DIR / "manifest.json")


def test_manifest_records_how_it_was_frozen():
    assert {"git_sha", "date", "specialist_model"} <= set(MANIFEST.frozen_with)


@pytest.mark.parametrize("case", MANIFEST.cases, ids=[c.name for c in MANIFEST.cases])
def test_every_case_loads_with_current_schemas(case):
    state = load_case_state(case, CASES_DIR)
    assert state["ticker"] == case.ticker and "synthesis" not in state
    if "valuation_failed" not in case.tags:
        assert state["valuation"].valuation_basis.startswith("FCFF")


def test_manifest_covers_archetypes_and_variants():
    tags = {t for c in MANIFEST.cases for t in c.tags}
    assert {"cyclical", "multi_class", "loss_making", "stability_qlys"} <= tags
    assert {c.variant for c in MANIFEST.cases} == {"base", "peers"}


def test_with_hypotheses_injects_three_hypotheses_without_touching_data():
    base = load_case_state(next(c for c in MANIFEST.cases if c.ticker == "QLYS"), CASES_DIR)
    hyp = with_hypotheses(base)
    assert len(hyp["checkpoint1_user_input"]["answers"]) == 3
    assert hyp["checkpoint2_user_input"]["growth_justification"]
    assert hyp["valuation"] == base["valuation"]
    assert not (base.get("checkpoint1_user_input") or {}).get("answers")     # el original no cambia


def test_with_hypotheses_reaches_the_expert_context():
    from due_diligence.agents.value_expert import _format_context
    base = load_case_state(next(c for c in MANIFEST.cases if c.ticker == "QLYS"), CASES_DIR)
    assert "## INVESTOR'S OWN HYPOTHESES" in _format_context(with_hypotheses(base))


def test_missing_state_file_fails_loud(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_case_state(EvalCase(name="x", ticker="X", variant="base", tags=[], state_file="nope.json"), tmp_path)


def test_select_cases_full_quick_and_hypotheses():
    from due_diligence.evals.dataset import HYP_SUFFIX, QUICK_SIZE, select_cases
    m = load_manifest(CASES_DIR / "manifest.json")
    full = select_cases(m, CASES_DIR, quick=False, hypotheses=True)
    names = [n for n, _ in full]
    assert names[:len(m.cases)] == [c.name for c in m.cases] and names[-1] == "QLYS-base" + HYP_SUFFIX
    assert "## INVESTOR'S OWN HYPOTHESES" not in str(dict(full)["QLYS-base"].get("checkpoint1_user_input"))
    assert dict(full)["QLYS-base" + HYP_SUFFIX]["checkpoint1_user_input"]["answers"]
    quick = select_cases(m, CASES_DIR, quick=True, hypotheses=False)
    assert len(quick) == QUICK_SIZE and all(n.endswith("-base") for n, _ in quick)


def test_states_for_rescore_resolves_hypothesis_cases():
    from due_diligence.evals.dataset import HYP_SUFFIX, states_for
    m = load_manifest(CASES_DIR / "manifest.json")
    st = states_for(["CALM-base", "QLYS-base" + HYP_SUFFIX], m, CASES_DIR)
    assert st["QLYS-base" + HYP_SUFFIX]["checkpoint2_user_input"]["growth_justification"]
    assert st["CALM-base"]["ticker"] == "CALM"
