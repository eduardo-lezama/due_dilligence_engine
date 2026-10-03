"""
Tests Phase 1b — Management Agent, Filings Agent, Market Agent y Competitive Agent.

Qué se testea aquí:
    Management Agent:
    - management_agent(): nodo LangGraph completo con todas las fuentes mockeadas.
    - _parse_llm_response(): parsing del JSON del LLM con casos felices y de error.
    - TavilyAdapter.search(): comportamiento cuando la API key no está configurada.

    Filings Agent:
    - filings_agent(): nodo LangGraph completo con las tres secciones 10-K mockeadas.
    - _parse_llm_response() del agente filings: casos felices, texto rodeando JSON, error.

Estrategia de mocking:
    Todas las llamadas externas (EDGAR, Tavily, LLM) se mockean con mocker.patch.
    Zero llamadas reales a APIs — los agentes son deterministas en tests.

Por qué top-level imports en los agentes:
    mocker.patch("due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text")
    funciona porque el import está a nivel de módulo. Si estuviera dentro de la función,
    el patch no alcanzaría el objeto correcto.
"""

import json
import pytest

from due_diligence.agents.management import management_agent
from due_diligence.agents.filings import filings_agent
from due_diligence.llm.errors import LLMProviderError, LLMSchemaError, LLMTruncatedError
from due_diligence.schemas.llm_outputs import (CitationOut, CompetitiveLLMOutput, FilingsLLMOutput,
                                               ManagementLLMOutput)
from due_diligence.agents.market import market_agent
from due_diligence.agents.competitive import competitive_agent
from due_diligence.schemas.reports import ManagementReport, FilingsRAGReport, MarketReport, CompetitiveReport


# --- Fixtures ---

@pytest.fixture
def minimal_state():
    """Estado mínimo para invocar management_agent — solo ticker obligatorio."""
    return {"ticker": "AAPL"}


def _mgmt_out(**over) -> ManagementLLMOutput:
    """Salida LLM tipada (ADR-032) mínima para el management agent."""
    base = dict(insider_ownership_pct=None, insider_ownership_citation=None, founder_ceo=None,
                ceo_tenure_years=None, ceo_tenure_citation=None, insider_transaction_pattern=None,
                compensation_structure_notes=None, capital_allocation_summary=None, citations=[])
    base.update(over)
    return ManagementLLMOutput(**base)


@pytest.fixture
def llm_response_valid():
    """Salida estructurada del caso feliz (antes: JSON en texto)."""
    return _mgmt_out(
        insider_ownership_pct=0.08,
        ceo_tenure_years=13.0,
        compensation_structure_notes="Tim Cook's compensation is primarily equity-based, vesting over 3 years with performance conditions tied to TSR vs. S&P 500.",
        capital_allocation_summary="Apple has returned over $600B to shareholders via buybacks since 2012. Capital allocation is consistent and shareholder-friendly.",
        citations=[CitationOut(
            claim="CEO compensation is primarily equity-based",
            source_url="DEF 14A 2024",
            excerpt="Mr. Cook's 2024 compensation consisted of $3M salary and $49M in equity awards",
            confidence=0.95,
            gaap_vs_nongaap_flag=None,
        )],
    )


# --- Tests: management_agent nodo LangGraph ---

def test_management_agent_returns_mgmt_key(mocker, minimal_state, llm_response_valid):
    """El nodo debe retornar un dict con la clave 'mgmt'."""
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text",
        return_value="Proxy statement text...",
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_form4_transactions",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_10k_item10",
        return_value="Item 10 directors bio text...",
    )
    mocker.patch(
        "due_diligence.agents.management.TavilyAdapter.search",
        return_value=[],
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = llm_response_valid
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_client)

    result = management_agent(minimal_state)

    assert "mgmt" in result


def test_management_agent_report_stub_false(mocker, minimal_state, llm_response_valid):
    """El reporte resultante debe tener stub=False — el agente corrió de verdad."""
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
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = llm_response_valid
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_client)

    result = management_agent(minimal_state)

    assert result["mgmt"].stub is False


def test_management_agent_populates_fields(mocker, minimal_state, llm_response_valid):
    """Los campos del reporte se populan correctamente desde la respuesta LLM."""
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text",
        return_value="Proxy text",
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_form4_transactions",
        return_value=[{"filer_name": "Tim Cook", "position": "CEO",
                       "transaction_date": "2025-01-10",
                       "shares": 500, "price_per_share": 230.0,
                       "transaction_type": "sell"}],
    )
    mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_10k_item10",
        return_value="Item 10 text",
    )
    mocker.patch("due_diligence.agents.management.TavilyAdapter.search", return_value=[])
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = llm_response_valid
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_client)

    result = management_agent(minimal_state)
    report: ManagementReport = result["mgmt"]

    # Sin cita verificada contra fuente primaria, los números del LLM se descartan (EC-69).
    assert report.insider_ownership_pct is None
    assert report.ceo_tenure_years is None
    # Determinista desde H1: len(form4_transactions), no lo que diga el LLM (ADR-005).
    assert report.form4_transactions_12m == 1
    # La cita no aparece en las fuentes mockeadas ("Proxy text") → se conserva, sin verificar.
    assert len(report.citations) == 1
    assert report.citations[0].verified is False and report.citations[0].confidence == 0.0


def test_management_agent_calls_edgar_and_tavily(mocker, minimal_state, llm_response_valid):
    """Verifica que el agente llama a las tres fuentes EDGAR y a Tavily."""
    mock_def14a = mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_def14a_text",
        return_value="",
    )
    mock_form4 = mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_form4_transactions",
        return_value=[],
    )
    mock_item10 = mocker.patch(
        "due_diligence.agents.management.EdgarManagementAdapter.fetch_10k_item10",
        return_value="",
    )
    mock_tavily = mocker.patch(
        "due_diligence.agents.management.TavilyAdapter.search",
        return_value=[],
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = llm_response_valid
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_client)

    management_agent(minimal_state)

    mock_def14a.assert_called_once_with("AAPL")
    mock_form4.assert_called_once_with("AAPL")
    mock_item10.assert_called_once_with("AAPL")
    assert mock_tavily.call_count >= 1  # may be 2 with transcript fallback (EC-48)


# --- Tests: structured() + fail loud (H1-T2c, ADR-032, EC-68) ---

@pytest.fixture
def mgmt_sources(mocker):
    """Fuentes de management sin red (EDGAR, 5.02, Tavily, Finnhub)."""
    p = "due_diligence.agents.management."
    mocker.patch(p + "EdgarManagementAdapter.fetch_def14a_text", return_value="")
    mocker.patch(p + "EdgarManagementAdapter.fetch_form4_transactions", return_value=[])
    mocker.patch(p + "EdgarManagementAdapter.fetch_10k_item10", return_value="")
    mocker.patch(p + "EdgarEventsAdapter.fetch_8k_events", return_value=[])
    mocker.patch(p + "TavilyAdapter.search", return_value=[])
    mocker.patch(p + "FinnhubAdapter.fetch_transcripts", return_value=[])


def test_management_agent_uses_structured_output(mocker, mgmt_sources):
    client = mocker.MagicMock()
    client.structured.return_value = _mgmt_out(capital_allocation_summary="Buybacks.")
    client.last_model_used = "gpt-4.1"
    mocker.patch("due_diligence.agents.management.get_client", return_value=client)
    rep = management_agent({"ticker": "TST"})["mgmt"]
    assert rep.stub is False and rep.error is None and rep.model_used == "gpt-4.1"
    assert rep.capital_allocation_summary == "Buybacks."
    assert rep.form4_transactions_12m == 0
    assert client.structured.call_args.args[0] is ManagementLLMOutput
    client.complete.assert_not_called()


def test_management_agent_fails_loud_on_llm_error(mocker, mgmt_sources):
    client = mocker.MagicMock()
    client.structured.side_effect = LLMSchemaError("refusal")
    mocker.patch("due_diligence.agents.management.get_client", return_value=client)
    rep = management_agent({"ticker": "TST"})["mgmt"]
    assert rep.stub is True and rep.error.startswith("LLMSchemaError")
    assert rep.compensation_structure_notes is None   # nada de "[Parse error]"


# --- Tests: TavilyAdapter degraded mode ---

def test_tavily_skips_when_no_api_key(mocker):
    """Si TAVILY_API_KEY no está configurada, search() devuelve lista vacía sin error."""
    mocker.patch(
        "due_diligence.tools.tavily.settings",
        tavily_api_key="",  # sin key
    )

    from due_diligence.tools.tavily import TavilyAdapter
    results = TavilyAdapter.search(["test query"])

    assert results == []


# ---------------------------------------------------------------------------
# Fixtures — Filings Agent
# ---------------------------------------------------------------------------

def _filings_out(**over) -> FilingsLLMOutput:
    """Salida LLM tipada (ADR-032) mínima para el filings agent; `over` sobrescribe campos."""
    base = dict(business_description="Makes widgets.", risk_factors_summary="Customer concentration.",
                mda_highlights="Revenue up 5%.", backlog_usd=None, backlog_citation=None,
                revenue_guidance=None, geographic_revenue_breakdown=None,
                key_regulatory_events=None, governance_red_flags=None,
                active_sec_investigation=None, material_weakness=None, governance_citation=None,
                citations=[])
    base.update(over)
    return FilingsLLMOutput(**base)


@pytest.fixture
def filings_llm_response_valid():
    """Salida estructurada que structured() devuelve para el filings agent (antes: JSON en texto)."""
    return _filings_out(
        business_description="Apple designs, manufactures and sells smartphones, personal computers, tablets, wearables and accessories. Revenue comes from product sales and services (App Store, iCloud, Apple Music). Hardware accounts for ~80% of revenue, services ~20% and growing.",
        risk_factors_summary="Key risks: intense competition in all product categories; reliance on third-party manufacturers in Asia (supply chain concentration); regulatory scrutiny of App Store practices in EU and US; dependence on iPhone revenue (~50% of total).",
        mda_highlights="FY2024 revenue of $391B, up 2% YoY, driven by services growth (+13%) offsetting flat iPhone revenue. Gross margin expanded to 45.9% on services mix shift. Management flagged FX headwinds and macroeconomic uncertainty in Greater China as key risks.",
        citations=[CitationOut(
            claim="Services revenue grew 13% YoY in FY2024",
            source_url="10-K Item 7",
            excerpt="Net sales for Services were $96.2 billion, representing an increase of 13% compared to 2023",
            confidence=0.95,
            gaap_vs_nongaap_flag=None,
        )],
    )


# --- Tests: filings_agent nodo LangGraph ---

def test_filings_agent_returns_filings_key(mocker, minimal_state, filings_llm_response_valid):
    """El nodo debe retornar un dict con la clave 'filings'."""
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_business",
        return_value="Business text...",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_risk_factors",
        return_value="Risk factors text...",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_mda",
        return_value="MDA text...",
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = filings_llm_response_valid
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_client)

    result = filings_agent(minimal_state)

    assert "filings" in result


def test_filings_agent_stub_false(mocker, minimal_state, filings_llm_response_valid):
    """El reporte resultante debe tener stub=False — el agente corrió de verdad."""
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
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = filings_llm_response_valid
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_client)

    result = filings_agent(minimal_state)

    assert result["filings"].stub is False


def test_filings_agent_populates_fields(mocker, minimal_state, filings_llm_response_valid):
    """Los campos del reporte se populan correctamente desde la respuesta LLM."""
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_business",
        return_value="Business text",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_risk_factors",
        return_value="Risk text",
    )
    mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_mda",
        # Contiene el excerpt literal de la cita del fixture → la cita se verifica (T3b).
        return_value="Net sales for Services were $96.2 billion, representing an increase of 13% compared to 2023.",
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = filings_llm_response_valid
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_client)

    result = filings_agent(minimal_state)
    report: FilingsRAGReport = result["filings"]

    assert report.business_description is not None
    assert "Apple" in report.business_description
    assert report.risk_factors_summary is not None
    assert report.mda_highlights is not None
    assert len(report.citations) == 1
    assert report.citations[0].verified is True
    assert report.citations[0].confidence == 0.95


def test_filings_agent_calls_all_three_edgar_methods(mocker, minimal_state, filings_llm_response_valid):
    """Verifica que el agente llama a las tres fuentes del 10-K."""
    mock_business = mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_business",
        return_value="",
    )
    mock_risks = mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_risk_factors",
        return_value="",
    )
    mock_mda = mocker.patch(
        "due_diligence.agents.filings.EdgarFilingsAdapter.fetch_10k_mda",
        return_value="",
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = filings_llm_response_valid
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_client)

    filings_agent(minimal_state)

    mock_business.assert_called_once_with("AAPL")
    mock_risks.assert_called_once_with("AAPL")
    mock_mda.assert_called_once_with("AAPL")


# --- Tests: structured() + fail loud (H1-T2b, ADR-032, EC-68) ---

@pytest.fixture
def edgar_texts(mocker):
    """Textos 10-K sintéticos para filings_agent (sin red)."""
    p = "due_diligence.agents.filings.EdgarFilingsAdapter."
    mocker.patch(p + "fetch_10k_business", return_value="We make widgets for industrial customers.")
    mocker.patch(p + "fetch_10k_risk_factors", return_value="One customer accounts for 40% of revenue.")
    mocker.patch(p + "fetch_10k_mda", return_value="Revenue increased 5% to $1,200 million.")


def test_filings_agent_uses_structured_output(mocker, edgar_texts):
    client = mocker.MagicMock()
    client.structured.return_value = _filings_out()
    client.last_model_used = "gpt-4.1"
    mocker.patch("due_diligence.agents.filings.get_client", return_value=client)
    report = filings_agent({"ticker": "WID"})["filings"]
    assert report.stub is False and report.error is None
    assert report.business_description == "Makes widgets."
    assert report.model_used == "gpt-4.1"
    assert client.structured.call_args.args[0] is FilingsLLMOutput
    client.complete.assert_not_called()


def test_filings_agent_fails_loud_on_llm_error(mocker, edgar_texts):
    client = mocker.MagicMock()
    client.structured.side_effect = LLMTruncatedError("gpt-4.1: truncated")
    mocker.patch("due_diligence.agents.filings.get_client", return_value=client)
    report = filings_agent({"ticker": "WID"})["filings"]
    assert report.stub is True
    assert report.error.startswith("LLMTruncatedError")
    assert report.mda_highlights is None        # nada de "[Parse error]" disfrazado de contenido


# ---------------------------------------------------------------------------
# Tests: Market Agent (tool-lane — sin LLM)
# ---------------------------------------------------------------------------

_POLYGON_DATA_FULL = {
    "price_current": 182.52,
    "price_52w_high": 199.62,
    "price_52w_low": 164.08,
    "market_cap": 2_800_000_000_000.0,
    "company_name": "Apple Inc.",
    "as_of_date": "2026-04-20",
}

_POLYGON_DATA_EMPTY = {
    "price_current": None,
    "price_52w_high": None,
    "price_52w_low": None,
    "market_cap": None,
    "company_name": None,
    "as_of_date": None,
}

_FINNHUB_NEWS = [
    {
        "headline": "Apple beats Q1 earnings expectations",
        "summary": "Apple reported Q1 revenue of $124B, above consensus of $121B.",
        "url": "https://example.com/apple-q1",
        "source": "Reuters",
        "datetime": 1745000000,
    }
]


def test_market_agent_returns_market_key(mocker, minimal_state):
    """El nodo debe retornar un dict con la clave 'market'."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=_FINNHUB_NEWS,
    )

    result = market_agent(minimal_state)

    assert "market" in result


def test_market_agent_stub_false(mocker, minimal_state):
    """El reporte resultante debe tener stub=False."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )

    result = market_agent(minimal_state)

    assert result["market"].stub is False


def test_market_agent_populates_metric_values(mocker, minimal_state):
    """Los MetricValues del reporte se construyen correctamente desde Polygon."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )

    result = market_agent(minimal_state)
    report: MarketReport = result["market"]

    assert report.price_current is not None
    assert report.price_current.value == 182.52
    assert report.price_current.unit == "USD"
    assert report.price_52w_high.value == 199.62
    assert report.price_52w_low.value == 164.08
    assert report.market_cap.value == 2_800_000_000_000.0


def test_market_agent_news_in_summary(mocker, minimal_state):
    """Las noticias de Finnhub aparecen en recent_news_summary."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=_FINNHUB_NEWS,
    )

    result = market_agent(minimal_state)

    assert "Apple beats Q1" in result["market"].recent_news_summary


def test_market_agent_degraded_when_no_polygon_data(mocker, minimal_state):
    """Si Polygon devuelve todos None, los campos numéricos del reporte son None."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_EMPTY,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )

    result = market_agent(minimal_state)
    report: MarketReport = result["market"]

    assert report.stub is False  # el agente corrió
    assert report.price_current is None
    assert report.market_cap is None


def test_market_agent_calls_polygon_and_finnhub(mocker, minimal_state):
    """Verifica que el agente llama a las dos fuentes de datos."""
    mock_polygon = mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_EMPTY,
    )
    mock_finnhub = mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )

    market_agent(minimal_state)

    mock_polygon.assert_called_once_with("AAPL")
    mock_finnhub.assert_called_once_with("AAPL")


# --- Tests: MCE-19 — News con Tavily + ventana adaptativa ---

def test_market_agent_skips_tavily_when_no_drawdown(mocker, minimal_state):
    """
    MCE-19: si stock está near 52w high (ratio >= 0.70), NO se activa Tavily.
    Optimización de tokens — Finnhub 30 días basta para stocks normales.
    """
    polygon_data = {
        "as_of_date": "2026-04-26",
        "price_current": 195.0,    # near 52w high
        "price_52w_high": 199.62,  # ratio 0.977
        "price_52w_low": 164.08,
        "market_cap": 2_800_000_000_000.0,
    }
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=polygon_data,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )
    tavily_spy = mocker.patch(
        "due_diligence.agents.market.TavilyAdapter.search",
        return_value=[],
    )

    market_agent(minimal_state)
    assert tavily_spy.call_count == 0  # NOT invoked


def test_market_agent_invokes_tavily_when_drawdown_detected(mocker, minimal_state):
    """
    MCE-19: si stock está en drawdown >30% desde 52w high (ratio <0.70), Tavily se invoca
    con queries específicas para encontrar el catalizador del crash.
    """
    polygon_data = {
        "as_of_date": "2026-05-10",
        "price_current": 38.37,    # drawdown desde 75.85
        "price_52w_high": 75.85,   # ratio 0.506 → < threshold 0.70
        "price_52w_low": 35.38,
        "market_cap": 500_000_000.0,
    }
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=polygon_data,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )
    tavily_spy = mocker.patch(
        "due_diligence.agents.market.TavilyAdapter.search",
        return_value=[
            {"title": "Tariff hits margin", "url": "url1", "content": "..."},
        ],
    )

    result = market_agent(minimal_state)
    assert tavily_spy.call_count == 1  # invoked
    # El summary debe contener el bloque "Extended news coverage"
    assert "Extended news coverage" in result["market"].recent_news_summary


# --- Tests: degraded mode de los adapters ---

def test_polygon_skips_when_no_api_key(mocker):
    """Si POLYGON_API_KEY no está configurada, devuelve dict con todos None."""
    mocker.patch(
        "due_diligence.tools.polygon.settings",
        polygon_api_key="",
    )

    from due_diligence.tools.polygon import PolygonAdapter
    result = PolygonAdapter.fetch_market_data("AAPL")

    assert result["price_current"] is None
    assert result["market_cap"] is None


def test_finnhub_skips_when_no_api_key(mocker):
    """Si FINNHUB_API_KEY no está configurada, devuelve lista vacía."""
    mocker.patch(
        "due_diligence.tools.finnhub.settings",
        finnhub_api_key="",
    )

    from due_diligence.tools.finnhub import FinnhubAdapter
    result = FinnhubAdapter.fetch_company_news("AAPL")

    assert result == []


# --- Tests: P2 — Finnhub fetch_quarterly_earnings (structured EPS actuals) ---

def test_finnhub_quarterly_earnings_skips_when_no_api_key(mocker):
    """P2: Sin FINNHUB_API_KEY, fetch_quarterly_earnings devuelve [] sin error."""
    mocker.patch(
        "due_diligence.tools.finnhub.settings",
        finnhub_api_key="",
    )
    from due_diligence.tools.finnhub import FinnhubAdapter
    result = FinnhubAdapter.fetch_quarterly_earnings("CGNX")
    assert result == []


def test_finnhub_quarterly_earnings_returns_structured_data(mocker):
    """P2: fetch_quarterly_earnings devuelve lista de dicts con campos EPS."""
    mock_raw = [
        {
            "period": "2025-10-01",
            "actual": 0.17,
            "estimate": 0.15,
            "surprise": 0.02,
            "surprisePercent": 13.3,
            "revenueActual": 225_000_000.0,
            "revenueEstimate": 215_000_000.0,
        },
        {
            "period": "2025-07-01",
            "actual": 0.14,
            "estimate": 0.13,
            "surprise": 0.01,
            "surprisePercent": 7.7,
            "revenueActual": None,
            "revenueEstimate": None,
        },
    ]
    mocker.patch(
        "due_diligence.tools.finnhub.settings",
        finnhub_api_key="test-key",
    )
    mock_client = mocker.MagicMock()
    mock_client.company_earnings.return_value = mock_raw
    mocker.patch("finnhub.Client", return_value=mock_client)

    from due_diligence.tools.finnhub import FinnhubAdapter
    result = FinnhubAdapter.fetch_quarterly_earnings("CGNX", limit=4)

    assert len(result) == 2
    assert result[0]["period"] == "2025-10-01"
    assert result[0]["actual"] == 0.17
    assert result[0]["surprisePercent"] == 13.3
    assert result[1]["revenueActual"] is None


def test_finnhub_quarterly_earnings_api_error_returns_empty(mocker):
    """P2: Error de API devuelve [] sin propagar la excepción."""
    mocker.patch(
        "due_diligence.tools.finnhub.settings",
        finnhub_api_key="test-key",
    )
    mock_client = mocker.MagicMock()
    mock_client.company_earnings.side_effect = Exception("API rate limit")
    mocker.patch("finnhub.Client", return_value=mock_client)

    from due_diligence.tools.finnhub import FinnhubAdapter
    result = FinnhubAdapter.fetch_quarterly_earnings("CGNX")
    assert result == []


def test_market_agent_wires_quarterly_eps_actuals(mocker, minimal_state):
    """P2: market_agent llama a fetch_quarterly_earnings y popula quarterly_eps_actuals."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_quarterly_earnings",
        return_value=[{"period": "2025-10-01", "actual": 0.17, "estimate": 0.15}],
    )

    result = market_agent(minimal_state)
    report: MarketReport = result["market"]

    assert len(report.quarterly_eps_actuals) == 1
    assert report.quarterly_eps_actuals[0]["actual"] == 0.17


def test_market_agent_quarterly_eps_actuals_empty_when_finnhub_fails(mocker, minimal_state):
    """P2: quarterly_eps_actuals es [] cuando Finnhub falla, sin interrumpir el agente."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_quarterly_earnings",
        return_value=[],
    )

    result = market_agent(minimal_state)
    assert result["market"].quarterly_eps_actuals == []


# --- Tests: P4 — Tavily fallback cuando 8-K extraction falla ---

def test_market_agent_tavily_fallback_when_earnings_text_missing(mocker, minimal_state):
    """P4: Si EDGAR no devuelve press release, Tavily busca earnings recientes."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_quarterly_earnings",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.market.EdgarEventsAdapter.fetch_earnings_press_release",
        return_value=None,  # EDGAR no encuentra el filing
    )
    mocker.patch(
        "due_diligence.agents.market.EdgarEventsAdapter.fetch_8k_events",
        return_value=[],
    )
    tavily_spy = mocker.patch(
        "due_diligence.agents.market.TavilyAdapter.search",
        return_value=[
            {"title": "CGNX Q1 2026 earnings", "url": "url1", "content": "Revenue $226M..."},
        ],
    )
    mocker.patch(
        "due_diligence.agents.market.settings",
        market_news_include_tavily=True,
        market_news_drawdown_threshold=0.70,
        events_lookback_months=12,
        edgar_8k_items="1.01,2.02,4.02,5.02,7.01,8.01",
    )

    result = market_agent(minimal_state)
    # Tavily fue llamado (una vez por el fallback trimestral — drawdown no aplica aquí)
    assert tavily_spy.call_count >= 1
    # El texto de earnings debe estar populado desde Tavily
    assert result["market"].quarterly_earnings_text is not None


def test_market_agent_no_tavily_fallback_when_8k_text_sufficient(mocker, minimal_state):
    """P4: Si 8-K devuelve texto suficiente (>500 chars), NO se invoca Tavily para earnings."""
    mocker.patch(
        "due_diligence.agents.market.PolygonAdapter.fetch_market_data",
        return_value=_POLYGON_DATA_FULL,
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_company_news",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.market.FinnhubAdapter.fetch_quarterly_earnings",
        return_value=[],
    )
    mocker.patch(
        "due_diligence.agents.market.EdgarEventsAdapter.fetch_earnings_press_release",
        return_value={"filing_date": "2026-05-06", "text": "A" * 600, "filing_url": ""},
    )
    mocker.patch(
        "due_diligence.agents.market.EdgarEventsAdapter.fetch_8k_events",
        return_value=[],
    )
    tavily_fallback_spy = mocker.patch(
        "due_diligence.agents.market._fetch_quarterly_earnings_tavily",
    )

    result = market_agent(minimal_state)
    # Con texto suficiente, el fallback NO se invoca
    assert tavily_fallback_spy.call_count == 0
    assert result["market"].quarterly_earnings_text == "A" * 600


# ---------------------------------------------------------------------------
# Tests: Competitive Agent
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path

# Cargamos el FinancialsReport desde el fixture real para tener datos coherentes
def _load_financials_fixture() -> "FinancialsReport":
    from due_diligence.tools.edgar import EdgarAdapter
    raw = _json.loads(
        (_Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text()
    )
    return EdgarAdapter.parse_financials(raw)


_COMPETITIVE_LLM_RESPONSE = CompetitiveLLMOutput(
    moat_classification="wide",
    moat_sources=["brand", "switching_costs"],
    citations=[CitationOut(
        claim="Apple's ROIC of 87% far exceeds typical consumer electronics peers (~15-25%)",
        source_url="Financial data: ROIC series",
        excerpt="ROIC series FY2025: 87.4%",
        confidence=0.95,
        gaap_vs_nongaap_flag=None,
    )],
)

@pytest.fixture
def competitive_state():
    """Estado con ticker y financials — el competitive_agent consume ambos."""
    return {
        "ticker": "AAPL",
        "financials": _load_financials_fixture(),
    }


def _mock_competitive(mocker, llm_response=_COMPETITIVE_LLM_RESPONSE):
    """Helper: mockea Tavily y LLM para tests del competitive_agent."""
    mocker.patch(
        "due_diligence.agents.competitive.TavilyAdapter.search",
        return_value=[],
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = llm_response
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_client)
    return mock_client


def test_competitive_agent_returns_competitive_key(mocker, competitive_state):
    """El nodo debe retornar un dict con la clave 'competitive'."""
    _mock_competitive(mocker)
    result = competitive_agent(competitive_state)
    assert "competitive" in result


def test_competitive_agent_stub_false(mocker, competitive_state):
    """El reporte resultante debe tener stub=False."""
    _mock_competitive(mocker)
    result = competitive_agent(competitive_state)
    assert result["competitive"].stub is False


def test_competitive_agent_moat_classification(mocker, competitive_state):
    """
    moat_classification se parsea correctamente, sujeto al moat calibration clamp (ADR-024).

    Con la rúbrica estricta de wide moat, el LLM puede decir "wide" pero el clamp
    post-hoc lo downgrade a "narrow" si los criterios cuantitativos fallan.
    """
    _mock_competitive(mocker)
    result = competitive_agent(competitive_state)
    report: CompetitiveReport = result["competitive"]
    # El LLM dice "wide", pero el clamp aplica con AAPL fixture (≤3 años ROIC + CAGR <4%)
    # → resultado esperado: "narrow" (downgraded). Es comportamiento correcto del clamp.
    assert report.moat_classification == "narrow"
    assert "brand" in report.moat_sources
    assert "switching_costs" in report.moat_sources


def test_competitive_agent_moat_clamp_downgrades_when_buyback_heavy(mocker, competitive_state):
    """
    Fix #1 / EC-28: si LLM dice "wide" pero buybacks ≥ 70% del FCF, downgrade a narrow.
    Mismo patrón que sizing clamp en EC-24 — defensa en profundidad.
    """
    from due_diligence.schemas.base import MetricValue

    # Inject buybacks que superan 70% del FCF en la fixture
    state = dict(competitive_state)
    financials = state["financials"]
    financials = financials.model_copy(update={
        "buybacks": [MetricValue(value=80_000_000, unit="USD", period="FY2025", source="XBRL")],
        "free_cash_flow": [MetricValue(value=100_000_000, unit="USD", period="FY2025", source="calculated")],
    })
    state["financials"] = financials

    _mock_competitive(mocker)
    result = competitive_agent(state)
    # 80M / 100M = 80% > 70% → criterio fallido → downgrade
    assert result["competitive"].moat_classification == "narrow"


def test_competitive_agent_citations_populated(mocker, competitive_state):
    """Las citations se validan contra los datos financieros enviados al LLM: "ROIC series
    FY2025: 87.4%" coincide (fuzzy) con la línea "ROIC series: FY2025: 87.4%, ..." del
    fixture AAPL → verified=True y source_url normalizado a la etiqueta real (EC-69)."""
    # El extracto se construye con el ROIC REAL del fixture (no un número fijo): al regenerar
    # fixtures en H1 T14 el ROIC de AAPL cambió (IC sin caja ni valores negociables) y un valor
    # escrito a mano rompía el test sin que el validador hubiera cambiado.
    roic0 = competitive_state["financials"].roic_series[0]
    response = _COMPETITIVE_LLM_RESPONSE.model_copy(update={"citations": [CitationOut(
        claim="Apple's ROIC far exceeds typical consumer electronics peers",
        source_url="Financial data: ROIC series",
        excerpt=f"ROIC series: {roic0.period}: {roic0.value:.1%}",
        confidence=0.95, gaap_vs_nongaap_flag=None)]})
    _mock_competitive(mocker, llm_response=response)
    result = competitive_agent(competitive_state)
    assert len(result["competitive"].citations) == 1
    c = result["competitive"].citations[0]
    assert c.verified is True and c.confidence == 0.95 and c.source_url == "Financial data"


def test_competitive_agent_calls_tavily(mocker, competitive_state):
    """Verifica que el agente llama a Tavily."""
    mock_tavily = mocker.patch(
        "due_diligence.agents.competitive.TavilyAdapter.search",
        return_value=[],
    )
    mock_client = mocker.MagicMock()
    mock_client.structured.return_value = _COMPETITIVE_LLM_RESPONSE
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_client)

    competitive_agent(competitive_state)
    mock_tavily.assert_called_once()


def test_competitive_agent_works_without_financials(mocker):
    """Si financials no está en el estado, el agente continúa sin fallar."""
    _mock_competitive(mocker)
    result = competitive_agent({"ticker": "AAPL"})  # sin financials
    assert result["competitive"].stub is False


# --- Tests: structured() + fail loud del competitive agent (H1-T2c, ADR-032) ---

def test_competitive_agent_uses_structured_output(mocker, competitive_state):
    client = _mock_competitive(mocker, CompetitiveLLMOutput(moat_classification="none",
                                                            moat_sources=[], citations=[]))
    rep = competitive_agent(competitive_state)["competitive"]
    assert rep.stub is False and rep.error is None and rep.moat_classification == "none"
    assert rep.peer_roic_comparison == []   # lo rellena peers_node después (ADR-015)
    assert client.structured.call_args.args[0] is CompetitiveLLMOutput
    client.complete.assert_not_called()


def test_competitive_agent_fails_loud_on_llm_error(mocker, competitive_state):
    client = _mock_competitive(mocker)
    client.structured.side_effect = LLMProviderError("503", provider="openai", fallback_eligible=True)
    rep = competitive_agent(competitive_state)["competitive"]
    assert rep.stub is True and rep.error.startswith("LLMProviderError")
    assert rep.moat_classification is None


def test_competitive_schema_rejects_unknown_moat():
    """El Literal del schema sustituye al 'unknown → none' manual: un valor fuera del
    contrato es un LLMSchemaError en el cliente, no un 'none' silencioso."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CompetitiveLLMOutput(moat_classification="unknown_value", moat_sources=[], citations=[])


# ---------------------------------------------------------------------------
# Tests: _word_in_text — word boundary regex (EC-59, MCE-28)
# ---------------------------------------------------------------------------

class TestWordInText:
    """
    _word_in_text usa \b para que "fall" no matchee "falls" o "falling".
    Esto elimina el false positive del thesis-break self-check (EC-59).
    """

    def _import(self):
        from due_diligence.agents.value_expert import _word_in_text
        return _word_in_text

    def test_exact_word_matches(self):
        """La palabra exacta (en infinitivo) sí hace match."""
        _word_in_text = self._import()
        assert _word_in_text("fall", "revenue will fall below threshold") is True

    def test_inflected_form_no_match(self):
        """PATH false positive: 'fall' NO debe matchear 'falling'."""
        _word_in_text = self._import()
        assert _word_in_text("fall", "revenue growth falling toward double digits") is False

    def test_third_person_no_match(self):
        """'fall' NO debe matchear 'falls'."""
        _word_in_text = self._import()
        assert _word_in_text("fall", "revenue falls below 10%") is False

    def test_compress_no_match_compressing(self):
        """CGNX EC-45 pattern: 'compress' NO debe matchear 'compressing'."""
        _word_in_text = self._import()
        assert _word_in_text("compress", "margins compressing due to pricing pressure") is False


# ---------------------------------------------------------------------------
# Tests: _thesis_break_self_check — dos etapas (EC-59, MCE-28)
# ---------------------------------------------------------------------------

class TestThesisBreakSelfCheck:
    """
    El thesis-break self-check tiene dos etapas:
    1. Python word-boundary pre-filter (_word_in_text): verifica frases de
       negative_condition_phrases en decision_frame Y en own_text (financial_quality/risks/growth).
    2. LLM semantic validation (solo si Stage 1 dispara).

    PATH false positive (EC-59): no debe disparar cuando decision_frame solo tiene
    formas progresivas ("falling"), no la raíz exacta ("fall").

    Firma: _thesis_break_self_check(report: ValueExpertReport, context: str) -> ValueExpertReport
    """

    def _make_report(
        self,
        decision_frame: str,
        financial_quality: str = "",
        risks: str = "",
    ) -> "ValueExpertReport":
        from due_diligence.schemas.reports import ValueExpertReport
        return ValueExpertReport(
            ticker="TEST",
            stub=False,
            decision_frame=decision_frame,
            financial_quality=financial_quality or None,
            risks=risks or None,
        )

    def test_no_warning_when_inflected_form_only(self, mocker):
        """
        PATH: decision_frame dice 'falling' y 'falls' (formas inflectadas).
        Word boundary impide Stage 1 — LLM ni se invoca, decision_frame sin warning.

        Stage 1 requiere que las palabras exactas de negative_condition_phrases estén
        en decision_frame. "fall" no matchea "falling" con \b — el trigger no dispara.
        """
        from due_diligence.agents.value_expert import _thesis_break_self_check

        # decision_frame con formas progresivas — "falling" no matchea la frase "revenue fall"
        decision_frame = (
            "§4 Revenue growing +13% YoY in FY2026. Strong demand signals.\n"
            "§7 Revenue growth falling toward double digits is a forward risk.\n"
            "§9 Thesis break: if FY2027 quarterly revenue falls below 10% growth.\n"
        )
        # own_text también con formas inflectadas — Stage 1 no puede completar el match
        report = self._make_report(
            decision_frame=decision_frame,
            risks="Revenue growth is falling toward double digits.",
        )

        llm_spy = mocker.patch(
            "due_diligence.agents.value_expert._validate_thesis_break_with_llm",
            return_value=False,
        )

        result = _thesis_break_self_check(report, context="")

        # Sin word-boundary exact match, el LLM no se invoca
        assert llm_spy.call_count == 0
        assert "THESIS-BREAK SELF-CHECK WARNING" not in (result.decision_frame or "")

    def test_warning_added_when_llm_confirms_currently_true(self, mocker):
        """
        Cuando Stage 1 dispara (frases exactas en decision_frame + own_text)
        Y el LLM confirma is_currently_true=True, el warning se añade al decision_frame.

        Stage 1: "margin decline" está en decision_frame (§9) y en risks (own_text) → trigger.
        Stage 2: LLM mock devuelve True → warning añadido.
        """
        from due_diligence.agents.value_expert import _thesis_break_self_check

        # decision_frame contiene "margin decline" como frase exacta
        decision_frame = (
            "§4 Operating margin: FY2024=12%, FY2025=8%, FY2026=7%.\n"
            "§9 Thesis break: if margin decline below 10%.\n"
        )
        # risks también menciona "margin decline" → own_text tiene ambas palabras
        report = self._make_report(
            decision_frame=decision_frame,
            risks="Margin decline has accelerated — operating leverage has inverted.",
        )

        mocker.patch(
            "due_diligence.agents.value_expert._validate_thesis_break_with_llm",
            return_value=True,  # LLM confirma: condición actualmente cumplida
        )

        result = _thesis_break_self_check(report, context="")

        assert "THESIS-BREAK SELF-CHECK WARNING" in (result.decision_frame or "")


# ---------------------------------------------------------------------------
# Tests: validación de citas en los nodos y gating de números (H1-T3b, EC-69)
# ---------------------------------------------------------------------------
from due_diligence.agents.filings import _build_report as build_filings  # noqa: E402
from due_diligence.agents.management import _build_report as build_mgmt  # noqa: E402

DEF14A = "All directors and executive officers as a group beneficially own 12.5% of outstanding shares."


def _cout(excerpt, source="DEF 14A"):
    return CitationOut(claim="c", source_url=source, excerpt=excerpt, confidence=0.9,
                       gaap_vs_nongaap_flag=None)


def test_form4_count_is_deterministic():
    rep = build_mgmt(_mgmt_out(), "TST", {"DEF 14A": DEF14A}, form4_count=7)
    assert rep.form4_transactions_12m == 7


def test_insider_pct_kept_only_with_verified_matching_citation():
    ok = build_mgmt(_mgmt_out(insider_ownership_pct=12.5,
                              insider_ownership_citation=_cout(DEF14A)), "TST", {"DEF 14A": DEF14A}, 0)
    assert ok.insider_ownership_pct == 12.5
    assert any(c.verified and "12.5%" in c.excerpt for c in ok.citations)
    no_cit = build_mgmt(_mgmt_out(insider_ownership_pct=12.5), "TST", {"DEF 14A": DEF14A}, 0)
    assert no_cit.insider_ownership_pct is None
    mismatch = build_mgmt(_mgmt_out(insider_ownership_pct=30.0,
                                    insider_ownership_citation=_cout(DEF14A)), "TST", {"DEF 14A": DEF14A}, 0)
    assert mismatch.insider_ownership_pct is None


def test_numeric_gate_rejects_web_only_support():
    """Un % de propiedad sacado de la web no puede quedar 'verificado' (fuentes primarias, ADR-011)."""
    web = "Analysts estimate insiders hold about 12.5% of the company's outstanding stock."
    rep = build_mgmt(_mgmt_out(insider_ownership_pct=12.5, insider_ownership_citation=_cout(web, "Tavily")),
                     "TST", {"DEF 14A": DEF14A, "Web (Tavily)": web}, 0)
    assert rep.insider_ownership_pct is None
    assert all(not c.verified for c in rep.citations if "Analysts estimate" in c.excerpt)


def test_ceo_tenure_requires_year_in_verified_excerpt():
    item10 = "Jane Doe has served as our Chief Executive Officer since 2015 and as a director since 2012."
    ok = build_mgmt(_mgmt_out(ceo_tenure_years=11.0, ceo_tenure_citation=_cout(item10, "10-K Item 10")),
                    "TST", {"10-K Item 10": item10}, 0)
    assert ok.ceo_tenure_years == 11.0
    no_year = "Jane Doe has served as our Chief Executive Officer for many years now."
    bad = build_mgmt(_mgmt_out(ceo_tenure_years=11.0, ceo_tenure_citation=_cout(no_year, "10-K Item 10")),
                     "TST", {"10-K Item 10": no_year}, 0)
    assert bad.ceo_tenure_years is None


def test_unverified_citations_marked_not_dropped():
    rep = build_mgmt(_mgmt_out(citations=[_cout("the CEO has been buying aggressively all year")]),
                     "TST", {"DEF 14A": DEF14A}, 0)
    assert len(rep.citations) == 1 and rep.citations[0].verified is False


def test_verified_text_citation_source_normalized_to_corpus_label():
    web = "The board added two independent directors with deep retail operating experience in 2024."
    rep = build_mgmt(_mgmt_out(citations=[_cout(web, "https://some-blog.example/post")]),
                     "TST", {"DEF 14A": DEF14A, "Web (Tavily)": web}, 0)
    assert rep.citations[0].verified is True and rep.citations[0].source_url == "Web (Tavily)"


def test_empty_corpus_marks_all_unverified_with_note():
    rep = build_mgmt(_mgmt_out(citations=[_cout("the CEO owns 12.5% of shares outstanding")]), "TST", {}, 0)
    assert all(not c.verified and "empty corpus" in c.verification_note for c in rep.citations)


def test_backlog_dropped_without_verified_citation():
    rep = build_filings(_filings_out(backlog_usd=1.27e9, backlog_citation=None), "TST",
                        {"10-K Item 7": "Backlog was $1.27 billion at year end."})
    assert rep.backlog_usd is None


def test_backlog_kept_with_verified_citation():
    mda = "Backlog was $1.27 billion at year end, up 12% year over year."
    rep = build_filings(_filings_out(backlog_usd=1.27e9,
                                     backlog_citation=_cout("Backlog was $1.27 billion at year end", "10-K Item 7")),
                        "TST", {"10-K Item 7": mda})
    assert rep.backlog_usd == 1.27e9
    assert any(c.verified and "1.27 billion" in c.excerpt for c in rep.citations)


def test_competitive_citations_validated_against_financials_and_web(mocker, competitive_state):
    from due_diligence.agents.competitive import _format_financials_for_prompt
    fin_line = _format_financials_for_prompt(competitive_state["financials"]).splitlines()[1]
    _mock_competitive(mocker, CompetitiveLLMOutput(moat_classification="narrow", moat_sources=["brand"],
                                                   citations=[_cout(fin_line, "Financial data"),
                                                              _cout("invented claim about pricing power everywhere")]))
    rep = competitive_agent(competitive_state)["competitive"]
    assert [c.verified for c in rep.citations] == [True, False]


# ---------------------------------------------------------------------------
# Tests: governance flags deterministas con cita verificada (H1-T4a, EC-70)
# ---------------------------------------------------------------------------
GOV_1A = ("In March 2026 we received a subpoena from the SEC Division of Enforcement in connection "
          "with an investigation of our revenue recognition practices. We also identified a material "
          "weakness in our internal control over financial reporting.")


def test_governance_flag_without_citation_is_dropped():
    rep = build_filings(_filings_out(active_sec_investigation=True, material_weakness=True,
                                     governance_citation=None), "TST", {"10-K Item 1A": GOV_1A})
    assert rep.active_sec_investigation is None and rep.material_weakness is None


def test_governance_flag_with_verified_citation_kept():
    cit = _cout("we received a subpoena from the SEC Division of Enforcement in connection with an "
                "investigation of our revenue recognition practices", "10-K Item 1A")
    rep = build_filings(_filings_out(active_sec_investigation=True, material_weakness=False,
                                     governance_citation=cit), "TST", {"10-K Item 1A": GOV_1A})
    assert rep.active_sec_investigation is True and rep.material_weakness is False
    assert any(c.verified and "subpoena" in c.excerpt for c in rep.citations)


def test_governance_flag_with_invented_citation_is_dropped():
    cit = _cout("The SEC has opened a formal investigation into the CEO's trading activity", "10-K Item 1A")
    rep = build_filings(_filings_out(active_sec_investigation=True, governance_citation=cit),
                        "TST", {"10-K Item 1A": GOV_1A})
    assert rep.active_sec_investigation is None
