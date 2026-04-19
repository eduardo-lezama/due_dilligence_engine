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

    Args:
        state: Estado del grafo — solo necesita state["ticker"].

    Returns:
        Dict parcial con: ticker (normalizado), cik, as_of_date, run_id,
        y campos cross-cutting inicializados (citations, unverified_claims, cost_usd).

    Raises:
        ValueError: Si el ticker no existe en EDGAR o no tiene 10-K disponible.
    """
    ticker = state["ticker"].upper()  # normalizar: "aapl" → "AAPL"

    # set_identity es obligatorio por la política de uso de EDGAR:
    # identifica quién hace las requests (nombre + email del sec_user_agent)
    set_identity(settings.sec_user_agent)

    try:
        company = Company(ticker)
        # zfill(10): convierte el entero 320193 → "0000320193" (formato estándar SEC)
        cik = str(company.cik).zfill(10)
        latest_10k = company.get_filings(form="10-K").latest()
        as_of_date = str(latest_10k.period_of_report)
    except Exception as e:
        # Fail loud (ADR-005): nunca continuar con un ticker inválido
        raise ValueError(f"{ticker}: could not resolve ticker in EDGAR — {e}")

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
        # Inicializar listas cross-cutting una sola vez aquí,
        # para que los agentes LLM puedan hacer append sin comprobar None
        "citations": [],
        "unverified_claims": [],
        "cost_usd": 0.0,
    }
