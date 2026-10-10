"""
Tests de scripts/freeze_contexts.py (H1 Task 0b, ampliado en H2-1 Task 1).

El script congela lo que ve el Value Expert para el harness de evals. Aquí se prueba con un grafo
falso: sin red, sin LLM. Comprueba el recorrido, los ficheros y la entrada del manifiesto.
"""
import json
from types import SimpleNamespace

from langgraph.types import Command

from due_diligence.agents.value_expert import _format_context
from due_diligence.schemas.reports import MarketReport
from scripts.freeze_contexts import freeze_context


class _FakeGraph:
    """Registra las llamadas a invoke y devuelve un estado fijo; el checkpoint 2 propone un WACC."""

    def __init__(self, values: dict, cp2_defaults: dict | None = None) -> None:
        self.values = values
        self.cp2_defaults = cp2_defaults
        self.calls: list[tuple[object, dict]] = []

    def invoke(self, payload, config=None, **kwargs):
        self.calls.append((payload, kwargs))
        return None

    def get_state(self, config):
        tasks = ()
        if self.cp2_defaults is not None and len(self.calls) == 2:      # tras reanudar el checkpoint 1
            itr = SimpleNamespace(value={"defaults": self.cp2_defaults})
            tasks = (SimpleNamespace(interrupts=(itr,)),)
        return SimpleNamespace(values=self.values, tasks=tasks)


def test_freeze_writes_context_state_and_manifest_and_stops_before_expert(tmp_path):
    state = {"ticker": "TST", "market": MarketReport(ticker="TST", stub=False)}
    graph = _FakeGraph(state, cp2_defaults={"wacc_pct": 8.4, "terminal_growth_pct": 2.5,
                                            "revenue_growth_yr1_pct": "auto (historical FCF CAGR or 7%)"})

    md_path = freeze_context("tst", tmp_path, graph=graph, today="2026-10-10")

    assert md_path == tmp_path / "TST-base.md"
    assert md_path.read_text(encoding="utf-8") == _format_context(state)
    saved = json.loads((tmp_path / "TST-base.state.json").read_text(encoding="utf-8"))
    assert saved["ticker"] == "TST"
    assert len(graph.calls) == 3
    assert isinstance(graph.calls[1][0], Command) and graph.calls[1][0].resume["proceed"] is True
    cp2 = graph.calls[2][0].resume
    # Defaults del propio nodo, sin el texto "auto (...)" que rompía ValuationInput; fecha fija.
    assert cp2["wacc_pct"] == 8.4 and "revenue_growth_yr1_pct" not in cp2 and cp2["valuation_date"] == "2026-10-10"
    assert graph.calls[2][1].get("interrupt_before") == ["value_expert"]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert {"git_sha", "date", "specialist_model"} <= set(manifest["frozen_with"])
    assert manifest["cases"] == [{"name": "TST-base", "ticker": "TST", "variant": "base", "tags": ["valuation_failed"],
                                  "state_file": "TST-base.state.json", "models": []}]


def test_peers_variant_passes_peers_and_replaces_manifest_entry(tmp_path):
    graph = _FakeGraph({"ticker": "AAPL"})
    freeze_context("AAPL", tmp_path, variant="peers", graph=graph, today="2026-10-10")
    freeze_context("AAPL", tmp_path, variant="peers", graph=_FakeGraph({"ticker": "AAPL"}), today="2026-10-10")
    assert graph.calls[1][0].resume["peers"] == "MSFT,GOOGL"
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in manifest["cases"]] == ["AAPL-peers"]          # reemplaza, no duplica
    assert "archetype" in manifest["cases"][0]["tags"]


def test_failed_valuation_is_tagged(tmp_path):
    from due_diligence.schemas.reports import ValuationReport
    state = {"ticker": "TST", "valuation": ValuationReport(ticker="TST", stub=True, error="ValuationInputError: x")}
    freeze_context("TST", tmp_path, graph=_FakeGraph(state), today="2026-10-10")
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert "valuation_failed" in manifest["cases"][0]["tags"]


def test_manifest_records_models_that_actually_answered(tmp_path, mocker):
    """El modelo configurado (claude-*) cae al fallback sin clave de Anthropic: hay que registrar el que
    respondió (visto al congelar H2-1: manifest decía claude-sonnet-4-5, respondió gpt-4o-mini)."""
    from due_diligence.llm.client import RunStats
    stats = RunStats()
    stats.add("gpt-4o-mini", 10, 10)
    client = mocker.MagicMock()
    client.get_run_stats.return_value = stats
    mocker.patch("scripts.freeze_contexts.get_client", return_value=client)
    freeze_context("TST", tmp_path, graph=_FakeGraph({"ticker": "TST"}), today="2026-10-10")
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cases"][0]["models"] == ["gpt-4o-mini"]
    assert manifest["frozen_with"]["specialist_model"] == "gpt-4o-mini"
    client.reset_run_stats.assert_called_once()


def test_git_sha_marks_dirty_tree(mocker):
    from scripts import freeze_contexts as fc
    run = mocker.patch("scripts.freeze_contexts.subprocess.run")
    run.side_effect = [mocker.MagicMock(stdout="abc1234\n"), mocker.MagicMock(stdout=" M src/x.py\n")]
    assert fc._git_sha() == "abc1234-dirty"
