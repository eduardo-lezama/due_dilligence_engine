"""
FinnhubAdapter — noticias de empresa desde Finnhub.

Qué hace este módulo:
    Thin wrapper sobre la API de Finnhub (finnhub-python SDK).
    Obtiene las noticias recientes de una empresa por ticker,
    filtradas a los últimos N días, listas para inyectar en
    el prompt del market_agent.

Por qué Finnhub para noticias:
    Finnhub indexa noticias de fuentes financieras confiables (Reuters,
    Bloomberg, etc.) y las asocia a tickers específicos — cero ambigüedad
    sobre si una noticia pertenece a la empresa analizada. Free tier:
    60 req/min, suficiente para uso personal.

Degraded mode:
    Si FINNHUB_API_KEY no está configurada, fetch_company_news devuelve
    lista vacía sin error — el market_agent produce recent_news_summary="N/A".

Uso en tests:
    Mockear FinnhubAdapter.fetch_company_news con mocker.patch.
"""

from datetime import date, timedelta
from typing import Any
import structlog

from due_diligence.config import settings

log = structlog.get_logger()


class FinnhubAdapter:
    @staticmethod
    def fetch_company_news(
        ticker: str,
        days: int = 30,
        max_articles: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Obtiene noticias recientes de la empresa en los últimos N días.

        Cada noticia tiene: headline, summary, url, datetime (Unix timestamp),
        source. Los artículos se ordenan por fecha descendente y se limitan
        a max_articles para no saturar el context window del market_agent.

        Si FINNHUB_API_KEY no está configurada, devuelve lista vacía (degraded mode).
        """
        if not settings.finnhub_api_key:
            log.warning("finnhub_skipped", reason="FINNHUB_API_KEY not configured")
            return []

        # Lazy import para no cargar el SDK si Finnhub no está configurado
        import finnhub  # type: ignore[import]

        client = finnhub.Client(api_key=settings.finnhub_api_key)

        today = date.today().isoformat()
        from_date = (date.today() - timedelta(days=days)).isoformat()

        try:
            log.debug("finnhub_news_fetch", ticker=ticker, from_date=from_date, to=today)
            # company_news devuelve una lista de dicts con: id, category, datetime,
            # headline, image, related, source, summary, url
            raw_news = client.company_news(ticker, _from=from_date, to=today)
        except Exception as e:
            log.warning("finnhub_news_failed", ticker=ticker, error=str(e))
            return []

        if not raw_news:
            log.info("finnhub_no_news", ticker=ticker, days=days)
            return []

        # Ordenar por datetime descendente (más reciente primero) y truncar
        sorted_news = sorted(raw_news, key=lambda x: x.get("datetime", 0), reverse=True)

        articles = []
        for article in sorted_news[:max_articles]:
            articles.append({
                "headline": article.get("headline", ""),
                "summary": article.get("summary", ""),
                "url": article.get("url", ""),
                "source": article.get("source", ""),
                "datetime": article.get("datetime", 0),
            })

        log.info("finnhub_news_complete", ticker=ticker, articles=len(articles))
        return articles

    @staticmethod
    def fetch_transcripts(ticker: str, quarters: int = 4) -> list[dict[str, Any]]:
        """
        Obtiene los últimos `quarters` earnings call transcripts de Finnhub (ADR-016).

        Cada item devuelto contiene:
            id, year, quarter, date, prepared_remarks (str), qa (str)

        Los turnos de prepared_remarks y Q&A se separan detectando el marcador
        "Q&A" en los primeros 80 caracteres del turno — heurístico suficiente para
        la mayoría de transcripts de Finnhub.

        Devuelve [] si FINNHUB_API_KEY no está configurada o hay error de red.
        """
        if not settings.finnhub_api_key:
            log.warning("finnhub_transcripts_skipped", reason="FINNHUB_API_KEY not configured")
            return []

        import finnhub  # type: ignore[import]

        client = finnhub.Client(api_key=settings.finnhub_api_key)

        try:
            listing = client.transcripts_list(ticker) or {}
        except Exception as e:
            log.warning("finnhub_transcripts_list_failed", ticker=ticker, error=str(e))
            return []

        # Ordenar por (year, quarter) descendente y tomar los N más recientes
        items = sorted(
            listing.get("transcripts", []),
            key=lambda x: (x.get("year", 0), x.get("quarter", 0)),
            reverse=True,
        )[:quarters]

        results: list[dict[str, Any]] = []
        for item in items:
            tid = item.get("id")
            if not tid:
                continue
            try:
                full = client.transcripts(tid) or {}
            except Exception as e:
                log.warning("finnhub_transcript_fetch_failed", id=tid, error=str(e))
                continue

            # Separar prepared_remarks de Q&A por presencia del marcador en el turno
            prepared: list[str] = []
            qa: list[str] = []
            in_qa = False
            for turn in full.get("transcript", []):
                text = " ".join(turn.get("speech", []))
                # Heurístico: si el turno empieza con "Q&A" o contiene el término es transición
                snippet = text[:80].lower()
                if not in_qa and ("q&a" in snippet or "question-and-answer" in snippet):
                    in_qa = True
                bucket = qa if in_qa else prepared
                bucket.append(f"{turn.get('speaker', '?')}: {text}")

            results.append({
                "id": tid,
                "year": item.get("year"),
                "quarter": item.get("quarter"),
                "date": item.get("time"),
                "prepared_remarks": "\n".join(prepared),
                "qa": "\n".join(qa),
            })

        log.info("finnhub_transcripts_fetched", ticker=ticker, count=len(results))
        return results


def format_news_for_prompt(articles: list[dict[str, Any]], max_articles: int = 5) -> str:
    """
    Formatea noticias de Finnhub como texto estructurado para el market_agent.

    Incluye headline + fuente + primer párrafo del summary.
    Limitado a max_articles para no saturar el context window.
    """
    if not articles:
        return "No recent company news available."

    lines = [f"Recent news (last 30 days, {min(len(articles), max_articles)} articles):"]
    for i, a in enumerate(articles[:max_articles], 1):
        summary_preview = a["summary"][:300] if a["summary"] else "(no summary)"
        lines.append(f"\n[{i}] {a['headline']}")
        lines.append(f"    Source: {a['source']} | URL: {a['url']}")
        lines.append(f"    {summary_preview}")

    return "\n".join(lines)
