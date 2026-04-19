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

    log.info("financials_node_complete", ticker=ticker, revenue_periods=len(report.revenue))

    # Devolver SOLO la clave que este nodo produce — no el estado completo.
    # LangGraph hace el merge automáticamente: state = {**state, **result}
    return {"financials": report}
