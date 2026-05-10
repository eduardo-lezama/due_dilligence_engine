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

from due_diligence.agents.management import management_agent, _parse_llm_response
from due_diligence.agents.filings import (
    filings_agent,
    _parse_llm_response as _parse_filings_response,
)
from due_diligence.agents.market import market_agent
from due_diligence.agents.competitive import (
    competitive_agent,
    _parse_llm_response as _parse_competitive_response,
)
from due_diligence.schemas.reports import ManagementReport, FilingsRAGReport, MarketReport, CompetitiveReport


# --- Fixtures ---

@pytest.fixture
def minimal_state():
    """Estado mínimo para invocar management_agent — solo ticker obligatorio."""
    return {"ticker": "AAPL"}


@pytest.fixture
def llm_response_valid():
    """Respuesta JSON bien formada que el LLM devuelve en el caso feliz."""
    return json.dumps({
        "insider_ownership_pct": 0.08,
        "ceo_tenure_years": 13.0,
        "form4_transactions_12m": 5,
        "compensation_structure_notes": "Tim Cook's compensation is primarily equity-based, vesting over 3 years with performance conditions tied to TSR vs. S&P 500.",
        "capital_allocation_summary": "Apple has returned over $600B to shareholders via buybacks since 2012. Capital allocation is consistent and shareholder-friendly.",
        "citations": [
            {
                "claim": "CEO compensation is primarily equity-based",
                "source_url": "DEF 14A 2024",
                "excerpt": "Mr. Cook's 2024 compensation consisted of $3M salary and $49M in equity awards",
                "confidence": 0.95,
                "gaap_vs_nongaap_flag": None
            }
        ]
    })


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
    mock_client.complete.return_value = llm_response_valid
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
    mock_client.complete.return_value = llm_response_valid
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
    mock_client.complete.return_value = llm_response_valid
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_client)

    result = management_agent(minimal_state)
    report: ManagementReport = result["mgmt"]

    assert report.insider_ownership_pct == 0.08
    assert report.ceo_tenure_years == 13.0
    assert report.form4_transactions_12m == 5
    assert len(report.citations) == 1
    assert report.citations[0].confidence == 0.95


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
    mock_client.complete.return_value = llm_response_valid
    mocker.patch("due_diligence.agents.management.get_client", return_value=mock_client)

    management_agent(minimal_state)

    mock_def14a.assert_called_once_with("AAPL")
    mock_form4.assert_called_once_with("AAPL")
    mock_item10.assert_called_once_with("AAPL")
    mock_tavily.assert_called_once()


# --- Tests: _parse_llm_response ---

def test_parse_valid_json():
    """Parsea correctamente un JSON válido con todos los campos."""
    raw = json.dumps({
        "insider_ownership_pct": 5.2,
        "ceo_tenure_years": 8.0,
        "form4_transactions_12m": 3,
        "compensation_structure_notes": "Equity-aligned compensation.",
        "capital_allocation_summary": "Disciplined buybacks and dividends.",
        "citations": [
            {
                "claim": "CEO owns 5.2% of shares",
                "source_url": "DEF 14A 2025",
                "excerpt": "Mr. Smith beneficially owns 5.2% of outstanding shares",
                "confidence": 0.9,
                "gaap_vs_nongaap_flag": None,
            }
        ],
    })

    report = _parse_llm_response(raw, "MSFT")

    assert report.stub is False
    assert report.insider_ownership_pct == 5.2
    assert report.ceo_tenure_years == 8.0
    assert len(report.citations) == 1


def test_parse_json_with_surrounding_text():
    """El parser extrae el JSON aunque el LLM añada texto antes o después."""
    raw = 'Here is the analysis:\n' + json.dumps({
        "insider_ownership_pct": 1.0,
        "ceo_tenure_years": None,
        "form4_transactions_12m": 0,
        "compensation_structure_notes": "N/A",
        "capital_allocation_summary": "N/A",
        "citations": [],
    }) + '\nEnd of response.'

    report = _parse_llm_response(raw, "XYZ")

    assert report.stub is False
    assert report.insider_ownership_pct == 1.0


def test_parse_invalid_json_returns_fallback():
    """Si el JSON es inválido, devuelve un ManagementReport parcial (no lanza excepción)."""
    raw = "Sorry, I cannot analyze this company."

    report = _parse_llm_response(raw, "UNKNOWN")

    # El agente no debe fallar — devuelve reporte degradado
    assert isinstance(report, ManagementReport)
    assert report.stub is False  # stub=False porque el agente SÍ corrió
    assert report.compensation_structure_notes is not None  # contiene el mensaje de error


def test_parse_missing_optional_fields():
    """Campos opcionales ausentes en el JSON se mapean a None sin error."""
    raw = json.dumps({
        "compensation_structure_notes": "Some notes.",
        "capital_allocation_summary": "Some summary.",
        "citations": [],
    })

    report = _parse_llm_response(raw, "TSLA")

    assert report.insider_ownership_pct is None
    assert report.ceo_tenure_years is None
    assert report.form4_transactions_12m is None


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

@pytest.fixture
def filings_llm_response_valid():
    """Respuesta JSON bien formada que el LLM devuelve para el filings agent."""
    return json.dumps({
        "business_description": "Apple designs, manufactures and sells smartphones, personal computers, tablets, wearables and accessories. Revenue comes from product sales and services (App Store, iCloud, Apple Music). Hardware accounts for ~80% of revenue, services ~20% and growing.",
        "risk_factors_summary": "Key risks: intense competition in all product categories; reliance on third-party manufacturers in Asia (supply chain concentration); regulatory scrutiny of App Store practices in EU and US; dependence on iPhone revenue (~50% of total).",
        "mda_highlights": "FY2024 revenue of $391B, up 2% YoY, driven by services growth (+13%) offsetting flat iPhone revenue. Gross margin expanded to 45.9% on services mix shift. Management flagged FX headwinds and macroeconomic uncertainty in Greater China as key risks.",
        "citations": [
            {
                "claim": "Services revenue grew 13% YoY in FY2024",
                "source_url": "10-K Item 7",
                "excerpt": "Net sales for Services were $96.2 billion, representing an increase of 13% compared to 2023",
                "confidence": 0.95,
                "gaap_vs_nongaap_flag": None,
            }
        ],
    })


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
    mock_client.complete.return_value = filings_llm_response_valid
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
    mock_client.complete.return_value = filings_llm_response_valid
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
        return_value="MDA text",
    )
    mock_client = mocker.MagicMock()
    mock_client.complete.return_value = filings_llm_response_valid
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_client)

    result = filings_agent(minimal_state)
    report: FilingsRAGReport = result["filings"]

    assert report.business_description is not None
    assert "Apple" in report.business_description
    assert report.risk_factors_summary is not None
    assert report.mda_highlights is not None
    assert len(report.citations) == 1
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
    mock_client.complete.return_value = filings_llm_response_valid
    mocker.patch("due_diligence.agents.filings.get_client", return_value=mock_client)

    filings_agent(minimal_state)

    mock_business.assert_called_once_with("AAPL")
    mock_risks.assert_called_once_with("AAPL")
    mock_mda.assert_called_once_with("AAPL")


# --- Tests: _parse_llm_response del filings agent ---

def test_filings_parse_valid_json():
    """Parsea correctamente un JSON válido con todos los campos."""
    raw = json.dumps({
        "business_description": "Sells software subscriptions to enterprises.",
        "risk_factors_summary": "Competition from cloud giants; customer churn.",
        "mda_highlights": "ARR grew 25% YoY; gross margin stable at 72%.",
        "citations": [
            {
                "claim": "ARR grew 25% YoY",
                "source_url": "10-K Item 7",
                "excerpt": "Annual Recurring Revenue increased 25% year-over-year",
                "confidence": 0.9,
                "gaap_vs_nongaap_flag": None,
            }
        ],
    })

    report = _parse_filings_response(raw, "CRM")

    assert report.stub is False
    assert report.business_description == "Sells software subscriptions to enterprises."
    assert len(report.citations) == 1


def test_filings_parse_json_with_surrounding_text():
    """El parser extrae el JSON aunque el LLM añada texto antes o después."""
    raw = "Here is my analysis:\n" + json.dumps({
        "business_description": "Makes semiconductors.",
        "risk_factors_summary": "Cyclicality, supply chain.",
        "mda_highlights": "Revenue down 12% on memory pricing.",
        "citations": [],
    }) + "\nEnd of analysis."

    report = _parse_filings_response(raw, "MU")

    assert report.stub is False
    assert "semiconductor" in report.business_description.lower()


def test_filings_parse_invalid_json_returns_fallback():
    """Si el JSON es inválido, devuelve FilingsRAGReport parcial sin lanzar excepción."""
    raw = "I cannot analyze this filing."

    report = _parse_filings_response(raw, "UNKNOWN")

    assert isinstance(report, FilingsRAGReport)
    assert report.stub is False
    assert report.mda_highlights is not None  # contiene el mensaje de error


def test_filings_parse_missing_optional_fields():
    """Campos opcionales ausentes en el JSON se mapean a None sin error."""
    raw = json.dumps({
        "citations": [],
    })

    report = _parse_filings_response(raw, "TSLA")

    assert report.business_description is None
    assert report.risk_factors_summary is None
    assert report.mda_highlights is None


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


_COMPETITIVE_LLM_RESPONSE = _json.dumps({
    "moat_classification": "wide",
    "moat_sources": ["brand", "switching_costs"],
    "moat_analysis": "Apple has a wide moat driven by its brand premium and tight ecosystem lock-in. Customers who own iPhone, Mac, iPad and AirPods face significant switching costs.",
    "widening_or_eroding": "The moat is widening. Services revenue growing at 13% YoY and gross margins expanding to 46% signal increasing switching costs and brand premium.",
    "disruption_assessment": "Android ecosystem remains the primary competitive threat. However, Apple's switch to custom silicon (M-series) reduces supply chain risk and widens performance moat.",
    "citations": [
        {
            "claim": "Apple's ROIC of 87% far exceeds typical consumer electronics peers (~15-25%)",
            "source_url": "Financial data: ROIC series",
            "excerpt": "ROIC series FY2025: 87.4%",
            "confidence": 0.95,
            "gaap_vs_nongaap_flag": None,
        }
    ],
})

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
    mock_client.complete.return_value = llm_response
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_client)


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
    """Las citations se construyen correctamente."""
    _mock_competitive(mocker)
    result = competitive_agent(competitive_state)
    assert len(result["competitive"].citations) == 1
    assert result["competitive"].citations[0].confidence == 0.95


def test_competitive_agent_calls_tavily(mocker, competitive_state):
    """Verifica que el agente llama a Tavily."""
    mock_tavily = mocker.patch(
        "due_diligence.agents.competitive.TavilyAdapter.search",
        return_value=[],
    )
    mock_client = mocker.MagicMock()
    mock_client.complete.return_value = _COMPETITIVE_LLM_RESPONSE
    mocker.patch("due_diligence.agents.competitive.get_client", return_value=mock_client)

    competitive_agent(competitive_state)
    mock_tavily.assert_called_once()


def test_competitive_agent_works_without_financials(mocker):
    """Si financials no está en el estado, el agente continúa sin fallar."""
    _mock_competitive(mocker)
    result = competitive_agent({"ticker": "AAPL"})  # sin financials
    assert result["competitive"].stub is False


# --- Tests: _parse_llm_response del competitive agent ---

def test_competitive_parse_valid_moat():
    """Clasifica correctamente los tres valores de moat."""
    for moat in ("wide", "narrow", "none"):
        raw = _json.dumps({
            "moat_classification": moat,
            "moat_sources": [],
            "citations": [],
        })
        report = _parse_competitive_response(raw, "TEST")
        assert report.moat_classification == moat


def test_competitive_parse_invalid_moat_defaults_to_none():
    """Un valor de moat no reconocido se convierte a 'none' sin excepción."""
    raw = _json.dumps({
        "moat_classification": "unknown_value",
        "moat_sources": [],
        "citations": [],
    })
    report = _parse_competitive_response(raw, "TEST")
    assert report.moat_classification == "none"


def test_competitive_parse_invalid_json_returns_stub_false():
    """JSON inválido devuelve CompetitiveReport parcial con stub=False."""
    report = _parse_competitive_response("not json at all", "ERR")
    assert isinstance(report, CompetitiveReport)
    assert report.stub is False


def test_competitive_peer_roic_empty_from_llm_parse():
    """_parse_llm_response siempre devuelve peer_roic_comparison vacío.
    Los peers los inyecta peers_node después — competitive_agent no tiene acceso
    a state['peer_financials'] porque corre en el fan-out antes de peers_node."""
    raw = _json.dumps({
        "moat_classification": "wide",
        "moat_sources": ["brand"],
        "peer_roic_comparison": [{"ticker": "MSFT", "roic": 0.35}],  # LLM intenta añadir
        "citations": [],
    })
    report = _parse_competitive_response(raw, "AAPL")
    assert report.peer_roic_comparison == []  # vacío aquí — se rellena en peers_node
