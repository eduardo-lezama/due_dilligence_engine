"""
Financials specialist node — agente tool-lane del grafo LangGraph.

Qué hace:
    Lee el ticker del estado, llama al EdgarAdapter para obtener datos
    financieros reales de SEC EDGAR vía XBRL, y escribe el FinancialsReport
    en el estado.

Por qué tool-lane (no LLM-lane):
    Los datos financieros son deterministas — vienen directamente del filing
    XBRL de la SEC. No hay interpretación cualitativa aquí. El LLM no toca
    estos números (ADR-005: zero hallucination numérica).

Estado actual: STUB — implementación real en Task 6.
"""

from due_diligence.graph.state import AnalysisState


def financials_node(state: AnalysisState) -> dict:
    """
    Stub del Financials specialist. Implementación real en Task 6.

    En producción: llamará a EdgarAdapter.fetch_raw() + parse_financials()
    y devolverá un FinancialsReport con datos XBRL reales.
    """
    # Stub: devuelve None para indicar que no hay datos todavía
    # Task 6 reemplazará esto con la llamada real al EdgarAdapter
    return {"financials": None}
