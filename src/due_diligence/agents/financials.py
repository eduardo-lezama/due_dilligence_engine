"""
Financials specialist node — agente tool-lane del grafo LangGraph.

Qué hace:
    Lee ticker del estado, llama a EdgarAdapter para obtener datos XBRL
    reales de SEC EDGAR, y escribe un FinancialsReport validado en el estado.

Tool-lane (no LLM):
    Los datos financieros son 100% deterministas — vienen del filing XBRL
    de la SEC. El LLM nunca toca estos números (ADR-005: zero hallucination
    numérica). Este nodo es puramente fetch + parse.

Nota sobre el import a nivel de módulo:
    EdgarAdapter se importa en el top del archivo (no lazy) para que
    pytest-mock pueda interceptarlo con mocker.patch().
"""

from __future__ import annotations

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.tools.edgar import EdgarAdapter

log = structlog.get_logger()


def _compute_revenue_cagr_5y(revenue_series: list) -> float | None:
    """
    Calcula el CAGR de revenue a 5 años (o el span disponible si hay < 6 puntos).

    La serie debe estar ordenada de más reciente (índice 0) a más antiguo.
    Usa el índice 0 como 'newest' y min(len-1, 4) como 'oldest' — máximo 4 años
    de diferencia (5 puntos = 4 intervalos).

    Retorna None si:
    - Hay menos de 2 puntos (no hay intervalo)
    - El valor más antiguo es <= 0 (evita división por cero y CAGR sin sentido)
    - El valor más reciente es <= 0

    Por qué 5y (índice 4):
        5 años captura un ciclo económico razonable sin distorsionar con datos
        pre-pandemia o de un período de hipercrecimiento excepcional.
        Es el horizonte estándar para CAGR histórico en análisis value.
    """
    if len(revenue_series) < 2:
        return None
    newest = revenue_series[0].value
    oldest_idx = min(len(revenue_series) - 1, 4)
    oldest = revenue_series[oldest_idx].value
    if oldest <= 0 or newest <= 0:
        return None
    n = oldest_idx  # número de años entre newest y oldest
    return (newest / oldest) ** (1 / n) - 1


def financials_node(state: AnalysisState) -> dict:
    """
    Obtiene datos financieros XBRL de EDGAR y devuelve un FinancialsReport.

    Args:
        state: Estado del grafo — usa state["ticker"].

    Returns:
        {"financials": FinancialsReport} — solo la clave que este nodo produce.
        LangGraph hace el merge con el resto del estado.
    """
    ticker = state["ticker"]
    log.info("financials_node_start", ticker=ticker)

    # fetch_raw: llamada de red (o mock en tests)
    # parse_financials: función pura — transforma dict → FinancialsReport
    raw = EdgarAdapter.fetch_raw(ticker)
    report = EdgarAdapter.parse_financials(raw)

    # Calcular CAGR histórico de revenue para anclaje de §6 Valuation (ADR-019).
    # Solo se añade al resultado si hay datos suficientes — no falla silenciosamente.
    cagr_5y = _compute_revenue_cagr_5y(report.revenue)
    if cagr_5y is not None:
        log.info("financials_node_cagr", ticker=ticker, cagr_5y=f"{cagr_5y:.1%}")
    else:
        log.info("financials_node_cagr_unavailable", ticker=ticker, revenue_periods=len(report.revenue))

    log.info("financials_node_complete", ticker=ticker, revenue_periods=len(report.revenue))

    out: dict = {"financials": report}
    if cagr_5y is not None:
        out["historical_cagr_5y"] = cagr_5y
    return out
