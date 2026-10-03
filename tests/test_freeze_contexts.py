"""
Tests de scripts/freeze_contexts.py (H1 Task 0b).

El script congela la línea base de lo que ve el Value Expert antes de cambiar
prompts. Aquí se prueba con un grafo falso: sin red, sin LLM.
"""
import json
from types import SimpleNamespace

from langgraph.types import Command

from due_diligence.agents.value_expert import _format_context
from due_diligence.schemas.reports import MarketReport
from scripts.freeze_contexts import freeze_context


class _FakeGraph:
    """Registra las llamadas a invoke y devuelve un estado fijo en get_state."""

    def __init__(self, values: dict) -> None:
        self.values = values
        self.calls: list[tuple[object, dict]] = []

    def invoke(self, payload, config=None, **kwargs):
        self.calls.append((payload, kwargs))
        return None

    def get_state(self, config):
        return SimpleNamespace(values=self.values)


def test_freeze_context_writes_context_and_state_and_stops_before_expert(tmp_path):
    state = {"ticker": "TST", "market": MarketReport(ticker="TST", stub=False)}
    graph = _FakeGraph(state)

    md_path = freeze_context("tst", tmp_path, graph=graph, today="2026-10-03")

    assert md_path == tmp_path / "TST-2026-10-03.md"
    assert md_path.read_text(encoding="utf-8") == _format_context(state)
    saved = json.loads((tmp_path / "TST-2026-10-03.state.json").read_text(encoding="utf-8"))
    assert saved["ticker"] == "TST" and saved["market"]["ticker"] == "TST"

    # 3 invocaciones: arranque, resume checkpoint1, resume checkpoint2 parando antes del Expert
    assert len(graph.calls) == 3
    assert graph.calls[0][0] == {"ticker": "TST"}
    assert isinstance(graph.calls[1][0], Command) and graph.calls[1][0].resume["proceed"] is True
    assert isinstance(graph.calls[2][0], Command) and graph.calls[2][0].resume["wacc_pct"] == 9.0
    assert graph.calls[2][1].get("interrupt_before") == ["value_expert"]
