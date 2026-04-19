"""
build_graph() — factory function que ensambla el grafo LangGraph.

Qué hace este módulo:
    Define una única función pública, build_graph(), que construye y compila
    el StateGraph con todos sus nodos y edges. Devuelve un CompiledGraph listo
    para invocar.

Por qué una factory function y no un objeto global:
    Los tests necesitan pasar MemorySaver (in-memory, sin SQLite) como
    checkpointer. Producción usa SqliteSaver (persiste estado entre runs).
    Si el grafo fuera un objeto global, no podríamos intercambiar el
    checkpointer entre llamadas.

Flujo Phase 1a (lineal):
    START → supervisor_node → financials_node → END

Flujo Phase 1b (paralelo, con Send()):
    START → supervisor_node → [Send() fan-out] → specialist_nodes → END

Este módulo solo ensambla — la lógica vive en agents/.
"""

from __future__ import annotations

from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from due_diligence.agents.financials import financials_node
from due_diligence.agents.supervisor import supervisor_node
from due_diligence.graph.state import AnalysisState


def build_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> object:
    """
    Construye y compila el grafo de análisis.

    Args:
        checkpointer: Saver para persistir el estado entre runs.
                      None = sin persistencia (útil en tests).
                      MemorySaver = in-memory (tests con thread_id).
                      SqliteSaver = producción.

    Returns:
        CompiledGraph listo para llamar con .invoke() o .stream().
    """
    # StateGraph recibe el TypedDict que define la forma del estado compartido
    builder = StateGraph(AnalysisState)

    # Registrar los nodos — cada nodo es una función (state) -> dict
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("financials", financials_node)

    # Edges: definen el orden de ejecución
    # START → supervisor → financials → END  (lineal en Phase 1a)
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", "financials")
    builder.add_edge("financials", END)

    # compile() valida el grafo (nodos alcanzables, edges válidos) y devuelve
    # un CompiledGraph con métodos invoke(), stream(), get_state(), etc.
    return builder.compile(checkpointer=checkpointer)
