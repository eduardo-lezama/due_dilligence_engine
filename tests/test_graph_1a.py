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
    # El grafo continúa al fan-out de especialistas hasta checkpoint1: sin estos mocks
    # el test hacía llamadas reales a OpenAI, Finnhub y EDGAR (H1, tests/CLAUDE.md).
    _mock_specialists(mocker)

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
    assert result["financials"].revenue[0].value == pytest.approx(416_161_000_000)


def _mock_specialists(mocker):
    """
    Mockea los 4 especialistas del fan-out (I/O + LLM) para que el grafo llegue a
    checkpoint1 sin ninguna llamada de red (tests/CLAUDE.md). Compartido por los tests
    de grafo de este archivo; antes el end-to-end hacía llamadas reales a OpenAI/Finnhub/EDGAR.
    """
    mocker.patch("due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text", return_value="")
    mocker.patch("due_diligence.agents.management.EdgarManagementAdapter.fetch_form4_transactions", return_value=[])
    mocker.patch("due_diligence.agents.management.EdgarManagementAdapter.fetch_10k_item10", return_value="")
    mocker.patch("due_diligence.agents.management.TavilyAdapter.search", return_value=[])
    mock_mgmt = mocker.MagicMock()
    from due_diligence.schemas.llm_outputs import CompetitiveLLMOutput, ManagementLLMOutput
    mock_mgmt.structured.return_value = ManagementLLMOutput(
        insider_ownership_pct=None, insider_ownership_citation=None, founder_ceo=None,
        ceo_tenure_years=None, ceo_tenure_citation=None, insider_transaction_pattern=None,
        compensation_structure_notes="N/A", capital_allocation_summary="N/A", citations=[])
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_mgmt)

    mocker.patch("due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_business", return_value="")
    mocker.patch("due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_risk_factors", return_value="")
    mocker.patch("due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_mda", return_value="")
    mock_filings = mocker.MagicMock()
    from due_diligence.schemas.llm_outputs import FilingsLLMOutput
    mock_filings.structured.return_value = FilingsLLMOutput(
        business_description=None, risk_factors_summary=None, mda_highlights=None,
        backlog_usd=None, backlog_citation=None, revenue_guidance=None,
        geographic_revenue_breakdown=None, key_regulatory_events=None,
        governance_red_flags=None, active_sec_investigation=None, material_weakness=None,
        governance_citation=None, citations=[])
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_filings)

    mocker.patch("due_diligence.agents.market.PolygonAdapter.fetch_market_data",
                 return_value={"price_current": None, "price_52w_high": None, "price_52w_low": None,
                               "market_cap": None, "company_name": None, "as_of_date": None})
    mocker.patch("due_diligence.agents.market.FinnhubAdapter.fetch_company_news", return_value=[])
    mocker.patch("due_diligence.agents.market.FinnhubAdapter.fetch_quarterly_earnings", return_value=[])
    mocker.patch("due_diligence.agents.market.EdgarEventsAdapter.fetch_8k_events", return_value=[])
    mocker.patch("due_diligence.agents.market.EdgarEventsAdapter.fetch_earnings_press_release", return_value=None)
    mocker.patch("due_diligence.agents.market.TavilyAdapter.search", return_value=[])

    mocker.patch("due_diligence.agents.competitive.TavilyAdapter.search", return_value=[])
    mock_comp = mocker.MagicMock()
    mock_comp.structured.return_value = CompetitiveLLMOutput(moat_classification="none",
                                                            moat_sources=[], citations=[])
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_comp)


def test_graph_1b_state_has_specialist_fields(mocker):
    """
    Con Phase 1b cableada, el estado contiene los 4 reports de los especialistas.

    Este test reemplaza el test de Phase 1a que verificaba que esos campos NO existían.
    Ahora que el fan-out está implementado, verificamos que SÍ se populan.
    Los agentes externos están mockeados — zero llamadas a EDGAR.
    """
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2025-09-27"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )
    _mock_specialists(mocker)

    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "test-run-1b-fields"}}
    graph.invoke({"ticker": "AAPL"}, config=config)

    # Inspeccionar el estado pausado en checkpoint1
    state = graph.get_state(config).values
    assert "mgmt" in state
    assert "filings" in state
    assert "market" in state
    assert "competitive" in state
