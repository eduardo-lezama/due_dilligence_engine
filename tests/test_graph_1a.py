"""
Tests para el grafo LangGraph de Phase 1a.

Verifican:
- build_graph() devuelve un grafo compilado válido (con y sin checkpointer).
- El grafo corre de extremo a extremo: supervisor → financials → estado final correcto.
- Los campos de agentes futuros (mgmt, filings, market, competitive) no existen todavía
  en el estado — son NotRequired y se añadirán en Phase 1b.

Todos los tests externos están mockeados: zero llamadas reales a EDGAR.
"""

import json
from pathlib import Path

import pytest

# Fixture cacheado AAPL FY2024 — permite tests reproducibles sin red
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())


def test_build_graph_returns_compiled_graph():
    """build_graph() con MemorySaver devuelve un CompiledGraph invocable."""
    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    graph = build_graph(checkpointer=MemorySaver())
    assert hasattr(graph, "invoke")


def test_build_graph_without_checkpointer():
    """build_graph() sin checkpointer también devuelve un grafo válido."""
    from due_diligence.graph.graph import build_graph

    graph = build_graph()
    assert hasattr(graph, "invoke")


def test_graph_1a_end_to_end(mocker):
    """Grafo completo Phase 1a: supervisor + financials con llamadas externas mockeadas."""
    # Mock del Supervisor: Company(ticker) devuelve CIK y fecha del 10-K
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    # Mock del Financials: fetch_raw devuelve el fixture cacheado en lugar de llamar a EDGAR
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph
    from due_diligence.schemas.base import FinancialsReport

    graph = build_graph(checkpointer=MemorySaver())
    # thread_id: identifica este run en el checkpointer — cada test usa uno distinto
    config = {"configurable": {"thread_id": "test-run-1a"}}
    result = graph.invoke({"ticker": "AAPL"}, config=config)

    # Verificar que el Supervisor escribió correctamente
    assert result["ticker"] == "AAPL"
    assert result["cik"] == "0000320193"
    assert result["as_of_date"] == "2024-09-28"
    assert "run_id" in result

    # Verificar que el Financials specialist escribió un report válido con datos reales
    assert isinstance(result["financials"], FinancialsReport)
    assert result["financials"].revenue[0].value == pytest.approx(391_035_000_000)


def test_graph_1a_state_has_no_stub_fields_for_later_agents(mocker):
    """mgmt, filings, market, competitive no existen en el estado — se añaden en Phase 1b."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-run-1a-b"}}
    result = graph.invoke({"ticker": "AAPL"}, config=config)

    # NotRequired: estos campos no deben existir hasta que los agentes de Phase 1b corran
    assert "mgmt" not in result
    assert "filings" not in result
    assert "market" not in result
    assert "competitive" not in result
