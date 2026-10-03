"""
Tests para el grafo LangGraph Phase 1b.

Qué se testea aquí:
    1. build_graph() compila con los 4 nuevos nodos de Phase 1b.
    2. _route_to_specialists() devuelve exactamente 4 Send() con los nodos correctos.
    3. El grafo Phase 1b completo corre de extremo a extremo con todos los agentes
       mockeados — verifica que el fan-out y el fan-in funcionan.
    4. checkpoint1_node llanza GraphInterrupt cuando se invoca sin checkpointer.
    5. El estado después del fan-out contiene los 4 reports de los especialistas.

Por qué se testea el checkpoint con GraphInterrupt y no con un run completo:
    interrupt() requiere un checkpointer para serializar el estado. En producción
    se usa SqliteSaver. En tests, MemorySaver soporta interrupts — pero para verificar
    que el interrupt se dispara correctamente, lo más simple es testear el nodo
    directamente (sin correr el grafo entero).

Todos los tests externos están mockeados: zero llamadas reales a EDGAR, Polygon, etc.
"""

import json
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())


# --- Tests: estructura del grafo ---

def test_build_graph_1b_has_all_specialist_nodes():
    """build_graph() Phase 1b incluye los 4 nodos de especialistas."""
    from due_diligence.graph.graph import build_graph

    graph = build_graph()
    node_names = set(graph.nodes.keys())

    assert "management" in node_names
    assert "filings" in node_names
    assert "market" in node_names
    assert "competitive" in node_names
    assert "checkpoint1" in node_names


def test_build_graph_1b_has_phase1a_nodes():
    """build_graph() Phase 1b mantiene los nodos de Phase 1a."""
    from due_diligence.graph.graph import build_graph

    graph = build_graph()
    node_names = set(graph.nodes.keys())

    assert "supervisor" in node_names
    assert "financials" in node_names


def test_route_to_specialists_returns_four_sends():
    """_route_to_specialists() debe devolver exactamente 4 Send()."""
    from langgraph.types import Send
    from due_diligence.graph.graph import _route_to_specialists

    # Estado mínimo — la función de routing solo necesita un dict
    state = {"ticker": "AAPL"}
    sends = _route_to_specialists(state)

    assert len(sends) == 4
    assert all(isinstance(s, Send) for s in sends)


def test_route_to_specialists_targets():
    """Los 4 Send() apuntan a los nodos especialistas correctos."""
    from due_diligence.graph.graph import _route_to_specialists

    state = {"ticker": "AAPL"}
    sends = _route_to_specialists(state)

    targets = {s.node for s in sends}
    assert targets == {"management", "filings", "market", "competitive"}


# --- Tests: checkpoint1_node ---

def test_checkpoint1_raises_outside_runnable_context():
    """
    checkpoint1_node llamado directamente (fuera del grafo) lanza RuntimeError.

    interrupt() requiere el contexto de un run de LangGraph para serializar el estado.
    Fuera de ese contexto, lanza RuntimeError('Called get_config outside of a runnable
    context'). Dentro del grafo (con MemorySaver/SqliteSaver), el grafo pausa y
    graph.invoke() retorna el estado actual sin propagar la excepción.
    """
    from due_diligence.agents.checkpoint import checkpoint1_node

    with pytest.raises(RuntimeError, match="runnable context"):
        checkpoint1_node({"ticker": "AAPL"})


# --- Tests: end-to-end Phase 1b con MemorySaver ---

def _make_competitive_response():
    """Salida tipada de structured() para competitive (ADR-032)."""
    from due_diligence.schemas.llm_outputs import CompetitiveLLMOutput
    return CompetitiveLLMOutput(moat_classification="wide",
                                moat_sources=["brand", "switching_costs"], citations=[])


def _make_management_response():
    """Salida tipada de structured() para management (ADR-032)."""
    from due_diligence.schemas.llm_outputs import ManagementLLMOutput
    return ManagementLLMOutput(
        insider_ownership_pct=0.08, insider_ownership_citation=None, founder_ceo=None,
        ceo_tenure_years=13.0, ceo_tenure_citation=None, insider_transaction_pattern=None,
        compensation_structure_notes="Equity-aligned.",
        capital_allocation_summary="Disciplined buybacks.", citations=[])


def _make_filings_response():
    """Salida tipada de structured() para filings (ADR-032)."""
    from due_diligence.schemas.llm_outputs import FilingsLLMOutput
    return FilingsLLMOutput(
        business_description="Apple designs and sells consumer electronics.",
        risk_factors_summary="Competition, supply chain concentration.",
        mda_highlights="Revenue grew 2% YoY.",
        backlog_usd=None, backlog_citation=None, revenue_guidance=None,
        geographic_revenue_breakdown=None, key_regulatory_events=None,
        governance_red_flags=None, citations=[],
    )


def test_graph_1b_fanout_pauses_at_checkpoint1(mocker):
    """
    El grafo Phase 1b corre supervisor → financials → 4 especialistas y pausa en checkpoint1.

    Con MemorySaver, graph.invoke() retorna normalmente cuando el grafo alcanza interrupt().
    El estado queda guardado en el checkpointer. get_state() muestra que el siguiente
    nodo pendiente es checkpoint1 (la pausa HITL).

    Así se confirma que el fan-out completó y el checkpoint se alcanzó correctamente.
    """
    # Mock Supervisor
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2025-09-27"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    # Mock Financials
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    # Mock Management
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text",
        return_value="",
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_form4_transactions",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_10k_item10",
        return_value="",
    )
    mocker.patch("due_diligence.agents.management.TavilyAdapter.search", return_value=[])
    mock_mgmt_client = mocker.MagicMock()
    mock_mgmt_client.structured.return_value = _make_management_response()
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_mgmt_client)

    # Mock Filings
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_business",
        return_value="",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_risk_factors",
        return_value="",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_mda",
        return_value="",
    )
    mock_filings_client = mocker.MagicMock()
    mock_filings_client.structured.return_value = _make_filings_response()
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_filings_client)

    # Mock Market (tool-lane: no LLM)
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value={
            "price_current": 182.52, "price_52w_high": 199.62,
            "price_52w_low": 164.08, "market_cap": 2.8e12,
            "company_name": "Apple Inc.", "as_of_date": "2026-04-20",
        },
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )

    # Mock Competitive
    mocker.patch("due_diligence.agents.competitive.TavilyAdapter.search", return_value=[])
    mock_comp_client = mocker.MagicMock()
    mock_comp_client.structured.return_value = _make_competitive_response()
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_comp_client)

    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "test-run-1b-fanout"}}

    # invoke() retorna normalmente cuando el grafo pausa en interrupt().
    # NO lanza GraphInterrupt — la excepción es capturada internamente por LangGraph.
    graph.invoke({"ticker": "AAPL"}, config=config)

    # Verificar que el grafo está pausado en checkpoint1 (pending next task)
    snapshot = graph.get_state(config)
    assert snapshot.next == ("checkpoint1",)


def test_graph_1b_state_has_all_four_reports_after_fanout(mocker):
    """
    Después del fan-out, el estado del grafo contiene los 4 reports de especialistas.

    Usa MemorySaver + get_state() para inspeccionar el estado serializado después
    del interrupt. Este es el patrón de producción: el usuario llama get_state()
    para ver los resultados antes de reanudar.
    """
    # Mock Supervisor
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2025-09-27"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    # Mock Financials
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    # Mock Management
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text",
        return_value="",
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_form4_transactions",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_10k_item10",
        return_value="",
    )
    mocker.patch("due_diligence.agents.management.TavilyAdapter.search", return_value=[])
    mock_mgmt_client = mocker.MagicMock()
    mock_mgmt_client.structured.return_value = _make_management_response()
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_mgmt_client)

    # Mock Filings
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_business",
        return_value="",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_risk_factors",
        return_value="",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_mda",
        return_value="",
    )
    mock_filings_client = mocker.MagicMock()
    mock_filings_client.structured.return_value = _make_filings_response()
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_filings_client)

    # Mock Market
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value={
            "price_current": 182.52, "price_52w_high": 199.62,
            "price_52w_low": 164.08, "market_cap": 2.8e12,
            "company_name": "Apple Inc.", "as_of_date": "2026-04-20",
        },
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )

    # Mock Competitive
    mocker.patch("due_diligence.agents.competitive.TavilyAdapter.search", return_value=[])
    mock_comp_client = mocker.MagicMock()
    mock_comp_client.structured.return_value = _make_competitive_response()
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_comp_client)

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.errors import GraphInterrupt
    from due_diligence.graph.graph import build_graph
    from due_diligence.schemas.reports import (
        ManagementReport, FilingsRAGReport, MarketReport, CompetitiveReport
    )

    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "test-run-1b-state"}}

    # Invocar — se detiene en checkpoint1
    try:
        graph.invoke({"ticker": "AAPL"}, config=config)
    except GraphInterrupt:
        pass  # esperado — el grafo pausó en checkpoint1

    # Inspeccionar el estado pausado
    snapshot = graph.get_state(config)
    state = snapshot.values

    # Los 4 especialistas deben haber escrito sus reports
    assert isinstance(state.get("mgmt"), ManagementReport)
    assert isinstance(state.get("filings"), FilingsRAGReport)
    assert isinstance(state.get("market"), MarketReport)
    assert isinstance(state.get("competitive"), CompetitiveReport)

    # Todos con stub=False
    assert state["mgmt"].stub is False
    assert state["filings"].stub is False
    assert state["market"].stub is False
    assert state["competitive"].stub is False
