"""
Supervisor node — primer nodo del grafo LangGraph.

Qué hace:
    Recibe el ticker del usuario, resuelve el CIK de la SEC, y escribe
    los campos de metadata (cik, as_of_date, run_id) en el estado.

Por qué un nodo supervisor:
    El grafo necesita un único punto de entrada que valide el input y
    resuelva identificadores antes de que los especialistas corran.
    El ticker ("AAPL") es lo que el usuario conoce; el CIK ("0000320193")
    es lo que EDGAR usa internamente. Esta resolución ocurre una sola vez aquí.

Estado actual: STUB — implementación real en Task 5.
"""

from due_diligence.graph.state import AnalysisState


def supervisor_node(state: AnalysisState) -> dict:
    """
    Stub del Supervisor. Implementación real en Task 5.

    En producción: resolverá ticker → CIK via edgartools y escribirá
    cik, as_of_date y run_id en el estado.
    """
    # Stub: devuelve campos mínimos para que el grafo pueda continuar
    return {
        "cik": "STUB",
        "as_of_date": "1970-01-01",
        "run_id": "stub-run",
    }
