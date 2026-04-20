"""
PolygonAdapter — datos de mercado en tiempo real desde Polygon.io.

Qué hace este módulo:
    Thin wrapper sobre la API REST de Polygon.io (polygon-api-client SDK).
    Obtiene precio actual (close del día anterior), rango 52 semanas y
    market cap para inyectar en MarketReport como MetricValues.

Por qué Polygon y no Yahoo Finance:
    ADR-011 prohíbe yfinance/scraping de Yahoo. Polygon.io es una API
    profesional con SLA, datos auditables y free tier suficiente para
    uso personal (~unlimited histórico, limitado en real-time). Market cap
    viene de TickerDetails, no calculado (más preciso para empresas con
    múltiples clases de acciones).

Degraded mode:
    Si POLYGON_API_KEY no está configurada, fetch_market_data devuelve un
    dict con todos los valores None — el market_agent produce un MarketReport
    parcial sin fallar. Mismo patrón que TavilyAdapter.

Uso en tests:
    Mockear PolygonAdapter.fetch_market_data con mocker.patch.
    Ver tests/test_agents_1b.py para ejemplos.
"""

from datetime import date, timedelta
from typing import Any, Optional
import structlog

from due_diligence.config import settings

log = structlog.get_logger()


class PolygonAdapter:
    @staticmethod
    def fetch_market_data(ticker: str) -> dict[str, Any]:
        """
        Obtiene precio actual, rango 52 semanas y market cap desde Polygon.

        Retorna dict con las siguientes claves (todas Optional):
            price_current: float — close del día anterior (proxy de precio actual)
            price_52w_high: float — máximo de los últimos 365 días
            price_52w_low: float — mínimo de los últimos 365 días
            market_cap: float — market cap en USD desde TickerDetails
            company_name: str — nombre oficial de la empresa
            as_of_date: str — fecha ISO del dato más reciente (YYYY-MM-DD)

        Si POLYGON_API_KEY no está configurada, todos los valores son None
        (degraded mode — el agente continúa sin datos de mercado).
        """
        if not settings.polygon_api_key:
            log.warning("polygon_skipped", reason="POLYGON_API_KEY not configured")
            return {
                "price_current": None,
                "price_52w_high": None,
                "price_52w_low": None,
                "market_cap": None,
                "company_name": None,
                "as_of_date": None,
            }

        # Lazy import — no cargar el SDK si Polygon no está configurado
        from polygon import RESTClient  # type: ignore[import]

        client = RESTClient(api_key=settings.polygon_api_key)
        today = date.today().isoformat()
        one_year_ago = (date.today() - timedelta(days=365)).isoformat()

        price_current: Optional[float] = None
        price_52w_high: Optional[float] = None
        price_52w_low: Optional[float] = None
        market_cap: Optional[float] = None
        company_name: Optional[str] = None

        # --- Precio actual: close del día anterior ---
        # get_previous_close_agg devuelve una lista en versiones recientes del SDK.
        try:
            prev_result = client.get_previous_close_agg(ticker, adjusted=True)
            # Compatibilidad: puede ser lista o objeto único según versión del SDK
            prev = prev_result[0] if isinstance(prev_result, list) else prev_result
            price_current = prev.close
            log.debug("polygon_prev_close", ticker=ticker, price=price_current)
        except Exception as e:
            log.warning("polygon_prev_close_failed", ticker=ticker, error=str(e))

        # --- Rango 52 semanas: iterar aggs diarios del último año ---
        try:
            highs: list[float] = []
            lows: list[float] = []
            for agg in client.list_aggs(
                ticker, 1, "day", one_year_ago, today, adjusted=True
            ):
                if agg.high is not None:
                    highs.append(agg.high)
                if agg.low is not None:
                    lows.append(agg.low)
            if highs:
                price_52w_high = max(highs)
            if lows:
                price_52w_low = min(lows)
            log.debug(
                "polygon_52w_range",
                ticker=ticker,
                high=price_52w_high,
                low=price_52w_low,
                days=len(highs),
            )
        except Exception as e:
            log.warning("polygon_52w_range_failed", ticker=ticker, error=str(e))

        # --- Market cap y nombre de empresa desde TickerDetails ---
        try:
            details = client.get_ticker_details(ticker)
            market_cap = details.market_cap
            company_name = details.name
            log.debug("polygon_ticker_details", ticker=ticker, market_cap=market_cap)
        except Exception as e:
            log.warning("polygon_ticker_details_failed", ticker=ticker, error=str(e))

        log.info(
            "polygon_fetch_complete",
            ticker=ticker,
            price=price_current,
            market_cap=market_cap,
        )

        return {
            "price_current": price_current,
            "price_52w_high": price_52w_high,
            "price_52w_low": price_52w_low,
            "market_cap": market_cap,
            "company_name": company_name,
            "as_of_date": today,
        }
