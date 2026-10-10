"""
Tests del runner del harness (H2-1 Task 3), sin red: el LLM se mockea respondiendo según el esquema
pedido (el Value Expert hace una segunda llamada, el validador de tesis, con otro esquema).
"""
from pathlib import Path

import pytest

from due_diligence.evals.dataset import load_case_state, load_manifest
from due_diligence.evals.runner import run_eval
from due_diligence.schemas.llm_outputs import ThesisBreakCheck, ValueExpertLLMOutput

CASES_DIR = Path(__file__).parent / "evals" / "cases"
_SECTIONS = ("business_understanding", "moat_analysis", "management_assessment", "financial_quality",
             "growth_reinvestment", "valuation_section", "risks", "psychological_lens", "decision_frame")


def _ve_out(**over) -> ValueExpertLLMOutput:
    base = {s: f"{s} text long enough to be a real section of the analysis." for s in _SECTIONS}
    base.update(verdict="HOLD", verdict_confidence=0.6, suggested_sizing_pct=2.0, verdict_rationale="Hold.")
    base.update(over)
    return ValueExpertLLMOutput(**base)


def _client(mocker, out):
    def structured(schema, *args, **kwargs):
        if schema is ThesisBreakCheck:
            return ThesisBreakCheck(is_currently_true=False, reason="ok")
        return out
    client = mocker.MagicMock()
    client.structured.side_effect = structured
    mocker.patch("due_diligence.agents.value_expert.get_client", return_value=client)
    return client


@pytest.fixture
def two_states():
    m = load_manifest(CASES_DIR / "manifest.json")
    cases = [c for c in m.cases if c.name in ("CALM-base", "QLYS-base")]
    return [(c.name, load_case_state(c, CASES_DIR)) for c in cases]


def test_records_keep_raw_and_final_outputs(mocker, two_states):
    _client(mocker, _ve_out(verdict="HOLD"))
    run = run_eval(two_states, repeat=3, expert_model=None, max_workers=2)
    assert len(run.records) == 6
    assert {(r.case, r.repeat) for r in run.records} == {(n, i) for n, _ in two_states for i in range(3)}
    assert all(r.raw["verdict"] == "HOLD" and r.report["verdict"] == "HOLD" and r.error is None for r in run.records)
    assert set(run.contexts) == {n for n, _ in two_states}       # contexto guardado una vez por caso
    assert run.repeat == 3 and run.cases == [n for n, _ in two_states] and len(run.prompt_sha) == 12


def test_llm_error_recorded_and_transport_flagged(mocker, two_states):
    from due_diligence.llm.errors import LLMProviderError
    c = _client(mocker, None)
    c.structured.side_effect = LLMProviderError("429", provider="openai", fallback_eligible=True)
    run = run_eval(two_states[:1], repeat=2, expert_model=None)
    assert all(r.error and r.error.startswith("LLMProviderError") and r.transport_error and r.raw is None
               for r in run.records)


def test_truncation_is_not_a_transport_error(mocker, two_states):
    from due_diligence.llm.errors import LLMTruncatedError
    c = _client(mocker, None)
    c.structured.side_effect = LLMTruncatedError("trunc")
    run = run_eval(two_states[:1], repeat=1, expert_model=None)
    assert run.records[0].error.startswith("LLMTruncatedError") and run.records[0].transport_error is False


def test_unexpected_exception_is_recorded_not_raised(mocker, two_states):
    mocker.patch("due_diligence.agents.value_expert.value_expert_agent", side_effect=RuntimeError("boom"))
    mocker.patch("due_diligence.evals.runner.value_expert_agent", side_effect=RuntimeError("boom"))
    run = run_eval(two_states[:1], repeat=1, expert_model=None)
    assert run.records[0].error.startswith("Crash: RuntimeError")


def test_expert_model_override_reaches_the_expert_call_and_is_restored(mocker, two_states):
    from due_diligence.agents import value_expert
    c = _client(mocker, _ve_out())
    before = value_expert.settings.openai_expert_model
    run = run_eval(two_states[:1], repeat=1, expert_model="gpt-test-model")
    assert c.structured.call_args_list[0].kwargs["model"] == "gpt-test-model"
    assert value_expert.settings.openai_expert_model == before
    assert run.expert_model_requested == "gpt-test-model"


def test_original_states_are_not_mutated(mocker, two_states):
    _client(mocker, _ve_out())
    name, state = two_states[0]
    snapshot = repr(state["valuation"])
    run_eval([(name, state)], repeat=2, expert_model=None)
    assert "synthesis" not in state and repr(state["valuation"]) == snapshot


def test_runstats_add_is_thread_safe():
    """Endurecimiento (no hay ROJO fiable bajo el GIL): 8 hilos sumando no pierden llamadas."""
    from concurrent.futures import ThreadPoolExecutor
    from due_diligence.llm.client import RunStats
    s = RunStats()
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda _: s.add("gpt-4.1", 10, 10), range(2000)))
    assert s.calls == 2000 and s.tokens_in == 20000 and s.calls_by_model == {"gpt-4.1": 2000}
