"""
Market Agent — datos de precio y noticias recientes.

Qué hace este módulo:
    Implementa el nodo LangGraph `market_agent` que produce un MarketReport
    con datos de mercado deterministas (precio, 52w range, market cap) desde
    Polygon.io y noticias recientes desde Finnhub.

    A diferencia de management_agent y filings_agent, este nodo es tool-lane:
    no llama al LLM. Los precios son datos exactos, no interpretables —
    la interpretación de valuación ocurre en el valuation_agent (Phase 1c).

Flujo del nodo:
    1. Fetch precio actual, 52w high/low y market cap via PolygonAdapter
    2. Fetch noticias recientes (30 días) via FinnhubAdapter
    3. Formatear noticias como texto legible (top 5 headlines + summary)
    4. Construir MarketReport con MetricValues validados (stub=False)
    5. Return {"market": report} → LangGraph merge en AnalysisState

Por qué no hay LLM aquí:
    CLAUDE.md agents/ distingue tool-lane (Financials, Market) de LLM-lane
    (Mgmt, Filings, Competitive). El market agent produce MetricValues que el
    valuation agent consumirá como inputs deterministas. Si interpretamos los
    precios aquí, el LLM mezclaría "datos" con "interpretación" — violando ADR-005.

Degraded mode:
    Si POLYGON_API_KEY o FINNHUB_API_KEY no están configuradas, el agente
    produce un MarketReport parcial con los campos disponibles. El nodo nunca
    falla silenciosamente — si ambas keys faltan, el report tiene todos los
    campos numéricos en None.

Por qué imports al nivel de módulo:
    mocker.patch en tests requiere imports top-level para que el patch
    alcance el objeto correcto.
"""

from __future__ import annotations

import structlog

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import MarketReport
from due_diligence.tools.edgar import EdgarEventsAdapter
from due_diligence.tools.finnhub import FinnhubAdapter, format_news_for_prompt
from due_diligence.tools.polygon import PolygonAdapter

log = structlog.get_logger()


def market_agent(state: AnalysisState) -> dict:
    """
    Nodo LangGraph: obtiene datos de mercado usando Polygon + Finnhub.

    Produce MetricValues para precio y market cap — datos deterministas
    sin interpretación LLM. Los MetricValues incluyen la fecha como
    'period' para trazabilidad temporal.
    """
    ticker = state["ticker"]
    log.info("market_agent_start", ticker=ticker)

    # --- 1. Datos de precio y market cap desde Polygon ---
    market_data = PolygonAdapter.fetch_market_data(ticker)
    as_of = market_data.get("as_of_date") or "unknown"

    # Convertir floats a MetricValue (con unidad y período para trazabilidad)
    price_current = _to_mv(market_data.get("price_current"), "USD", as_of)
    price_52w_high = _to_mv(market_data.get("price_52w_high"), "USD", as_of)
    price_52w_low = _to_mv(market_data.get("price_52w_low"), "USD", as_of)
    market_cap = _to_mv(market_data.get("market_cap"), "USD", as_of)

    # --- 2. Noticias recientes desde Finnhub ---
    news_articles = FinnhubAdapter.fetch_company_news(ticker)
    recent_news_summary = format_news_for_prompt(news_articles)

    # --- 3. Eventos materiales 8-K desde EDGAR (ADR-017) ---
    # Lookback controlado por settings.events_lookback_months (default 12).
    # Filtra Items 2.02 (earnings), 4.02 (restatement), 5.02 (mgmt changes).
    # Item 4.02 es señal roja crítica — restatement contable.
    material_events = EdgarEventsAdapter.fetch_8k_events(
        ticker,
        months=settings.events_lookback_months,
    )

    report = MarketReport(
        ticker=ticker,
        stub=False,
        price_current=price_current,
        price_52w_high=price_52w_high,
        price_52w_low=price_52w_low,
        market_cap=market_cap,
        recent_news_summary=recent_news_summary,
        material_events=material_events,
    )

    log.info(
        "market_agent_complete",
        ticker=ticker,
        price=market_data.get("price_current"),
        news_articles=len(news_articles),
        material_events=len(material_events),
    )

    return {"market": report}


def _to_mv(value: float | None, unit: str, period: str) -> MetricValue | None:
    """Convierte un float opcional a MetricValue, o None si el valor no está disponible."""
    if value is None:
        return None
    return MetricValue(value=value, unit=unit, period=period, source="Polygon")
