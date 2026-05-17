"""
TavilyAdapter — búsqueda web para agentes LLM.

Qué hace este módulo:
    Thin wrapper sobre la API de Tavily (tavily-python SDK).
    Recibe una lista de queries, ejecuta cada una contra Tavily,
    y devuelve fragmentos limpios (título + URL + contenido) listos
    para inyectar como contexto en un prompt LLM.

    También expone check_tavily_quota() para verificar el consumo mensual
    antes de arrancar un análisis — evita consumir otras APIs si Tavily
    está agotada (el usuario tiene una cuenta de backup en .env).

Por qué Tavily (ADR-012):
    Tavily está diseñado para agentes LLM: devuelve texto limpio ya
    parseado (no HTML crudo), incluye URL para Citation, y es
    provider-agnostic respecto al LLM. Free tier: 1000 req/mes.

Patrón fetch_raw / sin parse separado:
    A diferencia de EdgarAdapter, aquí no hay parse() separado porque
    los resultados de búsqueda no tienen un schema Pydantic fijo — son
    contexto semiestructurado para el LLM. La conversión a Citation
    ocurre en el agente que consume este adapter.

Uso en tests:
    Mockear TavilyAdapter.search() con mocker.patch — cero llamadas reales.
    Ver tests/test_agents_1b.py para ejemplos.
"""

import json
import urllib.error
import urllib.request
from typing import Any

import structlog

from due_diligence.config import settings

log = structlog.get_logger()


class TavilySearchError(Exception):
    """Excepción cuando Tavily falla o no está configurado."""
    pass


class TavilyQuotaExceededError(Exception):
    """Raised cuando el cuota mensual de Tavily está casi agotada (< buffer créditos)."""
    pass


def check_tavily_quota(buffer: int = 40) -> None:
    """
    Verifica que queden al menos `buffer` créditos en la cuenta de Tavily.

    Llama al endpoint GET /usage de Tavily. Si credits_used >= credits_limit - buffer,
    lanza TavilyQuotaExceededError para abortar el análisis antes de consumir
    recursos en EDGAR, Polygon, LLM, etc.

    Args:
        buffer: Créditos mínimos que deben quedar disponibles (default 40).
                Un análisis completo consume 9-14 créditos, así que buffer=40
                garantiza 2-3 análisis más antes de agotar el límite real.

    Raises:
        TavilyQuotaExceededError: Si credits_used >= credits_limit - buffer.

    No lanza excepción si:
        - TAVILY_API_KEY no está configurada (Tavily se omitirá de todas formas).
        - El endpoint /usage no responde o devuelve formato inesperado
          (falla gracefully — el análisis continúa sin garantía de cuota).
    """
    if not settings.tavily_api_key:
        return

    try:
        req = urllib.request.Request(
            "https://api.tavily.com/usage",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        # El endpoint puede devolver {"usage": {...}} o campos en top-level
        usage = data.get("usage", {})
        credits_used = (
            usage.get("credits_used") if isinstance(usage, dict)
            else data.get("credits_used")
        )
        credits_limit = (
            usage.get("credits_limit") if isinstance(usage, dict)
            else data.get("credits_limit")
        )

        if credits_used is None or credits_limit is None:
            log.warning("tavily_quota_unknown", response_keys=list(data.keys()))
            return

        remaining = credits_limit - credits_used
        log.info(
            "tavily_quota_check",
            credits_used=credits_used,
            credits_limit=credits_limit,
            remaining=remaining,
            buffer=buffer,
        )

        if credits_used >= credits_limit - buffer:
            raise TavilyQuotaExceededError(
                f"Tavily monthly quota nearly exhausted: "
                f"{credits_used}/{credits_limit} credits used "
                f"({remaining} remaining, buffer={buffer}). "
                f"Switch TAVILY_API_KEY in .env to your backup account and restart."
            )

    except TavilyQuotaExceededError:
        raise  # intencional — dejar que el caller la maneje
    except Exception as e:
        # Fallo de red o respuesta inesperada no aborta el análisis
        log.warning("tavily_quota_check_failed", error=str(e))


class TavilyAdapter:
    @staticmethod
    def search(
        queries: list[str] | list[tuple[str, str | None]],
        max_results_per_query: int = 3,
        time_range: str | None = "year",
    ) -> list[dict[str, Any]]:
        """
        Ejecuta múltiples búsquedas en Tavily y agrega los resultados.

        Args:
            queries: Lista de strings O lista de tuplas (query, time_range).
                     Si es string, usa el time_range global del parámetro.
                     Si es tupla, el segundo elemento sobreescribe el time_range global.
                     Ej: [("AAPL CEO founded year", None), ("AAPL ownership 2025", "year")]
            max_results_per_query: Resultados máximos por query (default 3).
            time_range: Filtro de antigüedad global: "day", "week", "month", "year", None.
                        Default "year". Sobreescribible por query individual.
                        Usar None para queries de contexto histórico (cuándo fundó la empresa,
                        patentes históricas) donde los artículos antiguos son relevantes.

        Returns:
            Lista de dicts con keys: url, title, content, score, query (query origen).
        """
        if not settings.tavily_api_key:
            log.warning("tavily_skipped", reason="TAVILY_API_KEY not configured")
            return []

        # Lazy import para no cargar la librería si Tavily no se usa
        from tavily import TavilyClient  # type: ignore[import]

        client = TavilyClient(api_key=settings.tavily_api_key)
        aggregated: list[dict[str, Any]] = []

        for query_item in queries:
            # Normalizar: string → (query, global time_range); tupla → (query, override)
            if isinstance(query_item, tuple):
                query, query_time_range = query_item
            else:
                query, query_time_range = query_item, time_range

            try:
                log.debug("tavily_search", query=query[:60])
                search_kwargs: dict[str, Any] = {
                    "max_results": max_results_per_query,
                    "topic": "finance",
                }
                if query_time_range is not None:
                    search_kwargs["time_range"] = query_time_range
                response = client.search(query, **search_kwargs)
                # Tavily devuelve {"results": [{"url":..., "title":..., "content":...}]}
                for result in response.get("results", []):
                    aggregated.append({
                        "url": result.get("url", ""),
                        "title": result.get("title", ""),
                        "content": result.get("content", ""),
                        "score": result.get("score", 0.0),
                        "query": query,  # guardamos la query para trazabilidad en Citation
                    })
            except Exception as e:
                # Fallo en una query individual no aborta las demás
                log.warning("tavily_query_failed", query=query[:60], error=str(e))

        log.info("tavily_search_complete", queries=len(queries), results=len(aggregated))
        return aggregated


def format_tavily_for_prompt(results: list[dict[str, Any]], max_chars_per_result: int = 800) -> str:
    """
    Convierte resultados de Tavily a texto estructurado para inyectar en un prompt.

    Trunca el contenido de cada resultado para no exceder el context window del LLM.
    """
    if not results:
        return "No web search results available."

    lines = []
    for i, r in enumerate(results, 1):
        content = r["content"][:max_chars_per_result]
        lines.append(f"[{i}] {r['title']}\nURL: {r['url']}\n{content}\n")

    return "\n".join(lines)
