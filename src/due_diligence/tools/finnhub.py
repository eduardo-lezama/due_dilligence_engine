"""
FinnhubAdapter — noticias y earnings trimestrales desde Finnhub.

Qué hace este módulo:
    Thin wrapper sobre la API de Finnhub (finnhub-python SDK).
    Obtiene:
      1. Noticias recientes de empresa (fetch_company_news) — texto libre.
      2. EPS / revenue actuals por quarter (fetch_quarterly_earnings) —
         datos estructurados beat/miss. Free tier: 60 req/min.
      3. Earnings call transcripts (fetch_transcripts, ADR-016).
      4. Sugerencia de competidores del mismo sector (fetch_company_peers, bloque B, B3).

Por qué Finnhub para noticias:
    Finnhub indexa noticias de fuentes financieras confiables (Reuters,
    Bloomberg, etc.) y las asocia a tickers específicos — cero ambigüedad
    sobre si una noticia pertenece a la empresa analizada. Free tier:
    60 req/min, suficiente para uso personal.

Por qué fetch_quarterly_earnings complementa el 8-K press release:
    El press release es texto libre difícil de parsear. El endpoint
    /stock/earnings de Finnhub devuelve EPS actual vs estimado, surprise%,
    y revenue (cuando disponible) por quarter — datos limpios y comparables
    para los últimos N quarters sin depender del HTML parsing de EDGAR.

Degraded mode:
    Si FINNHUB_API_KEY no está configurada, ambos métodos devuelven
    lista vacía sin error.

Uso en tests:
    Mockear FinnhubAdapter.fetch_company_news / fetch_quarterly_earnings
    con mocker.patch.
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
    def fetch_company_peers(ticker: str, limit: int = 5) -> list[str]:
        """Competidores sugeridos por Finnhub (/stock/peers: misma industria y país), sin el propio ticker.

        Bloque B (B3): en los informes de octubre de 2026 la comparación con competidores se saltaba siempre
        ("Peer comparison skipped by user") y el informe no respondía "¿barata frente a qué?". Finnhub solo
        PROPONE la lista: las cifras de cada competidor siguen saliendo de EDGAR XBRL y Polygon (peers_node)
        y el inversor puede cambiarla en el checkpoint 1. Sin clave o con error → [] (degraded mode, logueado).
        """
        if not settings.finnhub_api_key:
            log.warning("finnhub_peers_skipped", reason="FINNHUB_API_KEY not configured")
            return []
        import finnhub  # type: ignore[import]

        try:
            raw = finnhub.Client(api_key=settings.finnhub_api_key).company_peers(ticker) or []
        except Exception as e:  # sugerencia opcional: un fallo no bloquea el análisis
            log.warning("finnhub_peers_failed", ticker=ticker, error=str(e))
            return []
        subject = ticker.upper()
        peers = [str(p).upper() for p in raw if str(p).strip() and str(p).upper() != subject]
        log.info("finnhub_peers_fetched", ticker=ticker, peers=peers[:limit])
        return peers[:limit]

    @staticmethod
    def fetch_quarterly_earnings(
        ticker: str,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """
        Obtiene los últimos `limit` quarters de EPS actuals vs estimados via Finnhub.

        Endpoint: /stock/earnings (client.company_earnings en finnhub-python).
        Devuelve datos estructurados: period, actual EPS, estimated EPS, surprise%,
        y revenue (cuando disponible). Complementa el texto libre del 8-K press
        release con datos limpios y comparables sin depender del HTML parsing de EDGAR.

        Cada item devuelto contiene:
            period (str, YYYY-MM-DD), actual (float), estimate (float),
            surprise (float), surprisePercent (float),
            revenueActual (float | None), revenueEstimate (float | None)

        Devuelve [] si FINNHUB_API_KEY no está configurada, hay error de red,
        o el ticker no tiene datos de earnings.
        """
        if not settings.finnhub_api_key:
            log.warning("finnhub_earnings_skipped", reason="FINNHUB_API_KEY not configured")
            return []

        import finnhub  # type: ignore[import]

        client = finnhub.Client(api_key=settings.finnhub_api_key)

        try:
            log.debug("finnhub_earnings_fetch", ticker=ticker, limit=limit)
            raw = client.company_earnings(ticker, limit=limit) or []
        except Exception as e:
            log.warning("finnhub_earnings_failed", ticker=ticker, error=str(e))
            return []

        if not raw:
            log.info("finnhub_no_earnings", ticker=ticker)
            return []

        results: list[dict[str, Any]] = []
        for item in raw[:limit]:
            results.append({
                "period": item.get("period", ""),
                "actual": item.get("actual"),
                "estimate": item.get("estimate"),
                "surprise": item.get("surprise"),
                "surprisePercent": item.get("surprisePercent"),
                "revenueActual": item.get("revenueActual"),
                "revenueEstimate": item.get("revenueEstimate"),
            })

        log.info("finnhub_earnings_fetched", ticker=ticker, quarters=len(results))
        return results

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
