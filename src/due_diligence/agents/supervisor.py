"""
Supervisor node — primer nodo del grafo LangGraph.

Qué hace:
    Recibe {"ticker": "AAPL"} del usuario, resuelve el CIK de la SEC,
    extrae la fecha del 10-K más reciente, genera un run_id único, y
    escribe esos campos en el estado antes de que los especialistas corran.

Por qué es el primer nodo:
    El ticker es lo que el usuario conoce. El CIK ("0000320193") es el
    identificador interno de EDGAR — ningún otro nodo debería hacer esa
    resolución. Un único punto de entrada también valida que la empresa
    existe en EDGAR antes de lanzar el análisis completo.

Tool-lane (no LLM):
    Esta resolución es determinista — edgartools consulta el índice de
    la SEC directamente. No hay interpretación, no hay LLM.

Nota sobre imports a nivel de módulo:
    Company y set_identity se importan a nivel de módulo (no lazy) para
    que pytest-mock pueda interceptarlos con mocker.patch().
    Con lazy imports dentro de la función, patch no encuentra el atributo
    en el namespace del módulo y falla.
"""

from __future__ import annotations

import uuid

import structlog
from edgar import Company, set_identity  # type: ignore[import]

from due_diligence.config import settings
from due_diligence.graph.state import AnalysisState

log = structlog.get_logger()


def supervisor_node(state: AnalysisState) -> dict:
    """
    Valida el ticker, resuelve CIK, extrae as_of_date y genera run_id.

    Detección categorizada (EC-36):
        El error original "could not resolve ticker" era engañoso — agrupaba
        tres escenarios distintos (ticker inexistente, foreign 20-F filer,
        no-10-K filer) bajo el mismo mensaje. Ahora se distinguen con detección
        explícita y mensajes educativos, igual que EC-35 (financial firms).

    Args:
        state: Estado del grafo — solo necesita state["ticker"].

    Returns:
        Dict parcial con: ticker (normalizado), cik, as_of_date, run_id,
        y campos cross-cutting inicializados (citations, unverified_claims, cost_usd).

    Raises:
        TavilyQuotaExceededError: Si Tavily tiene < buffer créditos restantes.
        ValueError: Si el ticker no existe en EDGAR.
        DataUnavailableError: Si el ticker existe pero es foreign filer (20-F),
            partnership con K-1, o cualquier otra categoría no soportada.
    """
    from due_diligence.tools.edgar import DataUnavailableError
    from due_diligence.tools.tavily import check_tavily_quota

    ticker = state["ticker"].upper()  # normalizar: "aapl" → "AAPL"

    # Pre-flight: verificar cuota Tavily antes de consumir cualquier otra API.
    # Un análisis usa 9-14 créditos; si quedan <40 el usuario debe cambiar la key.
    check_tavily_quota()

    # set_identity es obligatorio por la política de uso de EDGAR:
    # identifica quién hace las requests (nombre + email del sec_user_agent)
    set_identity(settings.sec_user_agent)

    # Step 1: ticker resolution — ¿existe la empresa en EDGAR?
    try:
        company = Company(ticker)
        cik = str(company.cik).zfill(10)  # int → "0000320193"
    except Exception as e:
        raise ValueError(f"{ticker}: ticker not found in EDGAR — {e}")

    # Step 2: 10-K availability — ¿es un US domestic filer estándar?
    # patrón latest() aceptado (MCE-26, T5): aquí solo se comprueba que exista un 10-K y su
    # fecha; los NÚMEROS usan _get_latest_full_10k (excluye 10-K/A, EC-27/EC-87) en fetch_raw.
    latest_10k = company.get_filings(form="10-K").latest()
    if latest_10k is not None:
        as_of_date = str(latest_10k.period_of_report)
    else:
        # No hay 10-K — distinguir foreign filer (20-F) de otros casos
        latest_20f = company.get_filings(form="20-F").latest()
        if latest_20f is not None:
            # Foreign private issuer — EC-11 conocido, soporte diferido a Phase 3
            company_name = getattr(company, "name", ticker)
            industry = getattr(company, "industry", "Unknown")
            raise DataUnavailableError(
                f"{ticker} ({company_name}) is a foreign private issuer "
                f"(industry: {industry}). It files Form 20-F with the SEC, not 10-K. "
                f"This system's XBRL extraction is built for US GAAP 10-K filers — "
                f"20-F uses IFRS or local GAAP with different concept tagging, and "
                f"the analytical framework may differ (e.g. partnerships with K-1 "
                f"distributions, foreign-domiciled compounders). Foreign filers support "
                f"is deferred to Phase 3 (ver MCE-13). Last 20-F filed: "
                f"{latest_20f.filing_date} for FY {latest_20f.period_of_report}."
            )
        # Sin 10-K ni 20-F — caso raro (private company, SPAC pre-merger, recently delisted)
        raise DataUnavailableError(
            f"{ticker} (CIK {cik}) exists in EDGAR but has no 10-K or 20-F filings. "
            f"This could be a private company, pre-merger SPAC, recently delisted entity, "
            f"or non-operating shell. The system requires an annual report to extract "
            f"financials. Verify the ticker is currently public and reporting."
        )

    # UUID truncado a 12 chars: suficientemente único para identificar un run,
    # lo suficientemente corto para mostrarlo en el CLI sin ruido
    run_id = str(uuid.uuid4())[:12]

    log.info(
        "supervisor_complete",
        ticker=ticker,
        cik=cik,
        as_of_date=as_of_date,
        run_id=run_id,
    )

    return {
        "ticker": ticker,
        "cik": cik,
        "as_of_date": as_of_date,
        "run_id": run_id,
        # EC-111: el nombre legal de EDGAR. Antes ningún nodo lo escribía y market, management y
        # competitive caían al ticker: las búsquedas eran "ACMR ACMR competitors…" o "CALM stock
        # decline", que casaba con la palabra "calm". Lo consume también el filtro de relevancia.
        "company_name": str(getattr(company, "name", "") or "") or ticker,
        # Inicializar listas cross-cutting una sola vez aquí,
        # para que los agentes LLM puedan hacer append sin comprobar None
        "citations": [],
        "unverified_claims": [],
        "cost_usd": 0.0,
    }
