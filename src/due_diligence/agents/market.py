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
from due_diligence.tools.tavily import TavilyAdapter, format_tavily_for_prompt

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

    # --- 2b. Cobertura extendida con Tavily si stock en drawdown (MCE-19) ---
    # Finnhub limita a 30 días. Para stocks con drawdown >30% desde 52w high, el catalizador
    # típico (tariff hit, profit warning, guidance cut) suele estar fuera de esa ventana.
    # Tavily complementa con queries targeting sin filtro temporal estricto.
    company_name = state.get("company_name", ticker)  # type: ignore[typeddict-item]
    drawdown_news = _fetch_drawdown_context_news(
        ticker=ticker,
        company_name=company_name,
        price_current=price_current,
        price_52w_high=price_52w_high,
    )
    if drawdown_news:
        recent_news_summary = (
            f"{recent_news_summary}\n\n"
            f"## Extended news coverage (drawdown context — Tavily, last 12 months)\n"
            f"{drawdown_news}"
        )

    # --- 3. Eventos materiales 8-K desde EDGAR (ADR-017 + MCE-20) ---
    # Lookback controlado por settings.events_lookback_months (default 12).
    # Items configurables via EDGAR_8K_ITEMS — default ampliado a 6 items críticos:
    # 1.01 (M&A), 2.02 (earnings), 4.02 (restatement RED FLAG), 5.02 (mgmt changes),
    # 7.01 (Reg FD / guidance updates), 8.01 (other material events incluyendo tariff disclosures).
    items_filter = [s.strip() for s in settings.edgar_8k_items.split(",") if s.strip()]
    material_events = EdgarEventsAdapter.fetch_8k_events(
        ticker,
        items=items_filter,
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


def _fetch_drawdown_context_news(
    ticker: str,
    company_name: str,
    price_current: MetricValue | None,
    price_52w_high: MetricValue | None,
) -> str:
    """
    Fetch noticias extendidas via Tavily cuando el stock está en drawdown (MCE-19).

    Por qué adaptativo y no siempre:
        Finnhub (30 días) cubre el news flow reciente para stocks normales.
        Para stocks con caídas materiales (drawdown >30%), el catalizador suele
        estar 2-6 meses atrás — fuera del lookback de Finnhub. Ejemplos:
            - BBW: tariff disclosure de Dec 2025 invisible al sistema (run May 2026).
            - Cualquier stock con profit warning, guidance cut, regulatory event antiguo.

        Activar Tavily SIEMPRE costaría tokens innecesarios para stocks near-high
        donde Finnhub ya cubre lo relevante. Activación condicional preserva eficiencia.

    Returns:
        String formateado con artículos Tavily, o "" si no aplica (no drawdown
        o Tavily no configurado o feature flag disabled).
    """
    if not settings.market_news_include_tavily:
        return ""
    if price_current is None or price_52w_high is None or price_52w_high.value <= 0:
        return ""

    ratio = price_current.value / price_52w_high.value
    if ratio >= settings.market_news_drawdown_threshold:
        log.debug(
            "market_drawdown_news_skipped",
            ticker=ticker,
            ratio=f"{ratio:.2f}",
            threshold=settings.market_news_drawdown_threshold,
        )
        return ""

    drawdown_pct = (1 - ratio) * 100
    log.info(
        "market_drawdown_detected",
        ticker=ticker,
        drawdown_pct=f"{drawdown_pct:.0f}%",
        triggering_extended_news=True,
    )

    # Queries targeting catalizadores típicos de drawdowns. time_range=None para máxima
    # cobertura histórica; el LLM downstream filtra por relevancia.
    queries = [
        (f"{company_name} stock decline drop guidance miss profit warning", None),
        (f"{ticker} {company_name} tariff supply chain margin compression", None),
        (f"{ticker} {company_name} regulatory risk SEC investigation", None),
    ]
    try:
        results = TavilyAdapter.search(queries)
        return format_tavily_for_prompt(results, max_chars_per_result=600)
    except Exception as e:
        log.warning("market_tavily_drawdown_failed", ticker=ticker, error=str(e))
        return ""
