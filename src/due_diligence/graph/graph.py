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
    START → supervisor_node → financials_node
          → [Send() fan-out a 4 especialistas en paralelo]
          → management_agent, filings_agent, market_agent, competitive_agent
          → checkpoint1_node (HITL: pausa para revisión humana)
          → END

    Cada Send() crea una copia del estado y la pasa al especialista.
    LangGraph recoge los 4 dicts de retorno y los merge en el estado
    compartido automáticamente. Los edges de los 4 especialistas convergen
    en checkpoint1 (fan-in implícito: LangGraph espera a que todos terminen).

Flujo Phase 1c (pendiente):
    → valuation_agent → synthesis_agent → checkpoint2 → critic_agent → END

Este módulo solo ensambla — la lógica vive en agents/.
"""

from __future__ import annotations

from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from due_diligence.agents.checkpoint import checkpoint1_node, checkpoint2_node
from due_diligence.agents.peers import peers_node
from due_diligence.agents.competitive import competitive_agent
from due_diligence.agents.filings import filings_agent
from due_diligence.agents.financials import financials_node
from due_diligence.agents.management import management_agent
from due_diligence.agents.market import market_agent
from due_diligence.agents.supervisor import supervisor_node
from due_diligence.agents.valuation import valuation_agent
from due_diligence.agents.archiver import archiver_node
from due_diligence.agents.critic import critic_agent
from due_diligence.agents.value_expert import value_expert_agent
from due_diligence.graph.state import AnalysisState


def _route_to_specialists(state: AnalysisState) -> list[Send]:
    """
    Función de routing del fan-out: decide a qué nodos enviar el estado.

    LangGraph llama a esta función después de que financials_node termina.
    Devuelve una lista de Send() — cada uno lanza un especialista en paralelo
    con una copia del estado actual.

    Por qué Send() y no edges normales:
        Los edges normales son estáticos (A → B siempre). Send() es dinámico:
        permite pasar una copia específica del estado a cada destino, habilitando
        paralelismo real. LangGraph ejecuta todos los Send() concurrentemente y
        hace el merge de resultados cuando todos terminan.

    Returns:
        list[Send] con los 4 especialistas Phase 1b.
    """
    return [
        Send("management", state),
        Send("filings", state),
        Send("market", state),
        Send("competitive", state),
    ]


def build_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> object:
    """
    Construye y compila el grafo de análisis Phase 1b.

    Args:
        checkpointer: Saver para persistir el estado entre runs.
                      None = sin persistencia (útil en tests sin checkpointer).
                      MemorySaver = in-memory (tests con thread_id).
                      SqliteSaver = producción.

    Returns:
        CompiledGraph listo para llamar con .invoke() o .stream().

    Nota sobre interrupt() y checkpointer:
        checkpoint1_node usa interrupt(), que requiere un checkpointer para
        serializar el estado. En tests sin checkpointer, interrupt() lanza
        GraphInterrupt que se puede capturar con pytest.raises().
        En producción, SqliteSaver persiste el estado entre reanudaciones.
    """
    builder = StateGraph(AnalysisState)

    # --- Nodos Phase 1a ---
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("financials", financials_node)

    # --- Nodos Phase 1b: especialistas en paralelo ---
    builder.add_node("management", management_agent)
    builder.add_node("filings", filings_agent)
    builder.add_node("market", market_agent)
    builder.add_node("competitive", competitive_agent)

    # --- Nodos HITL Phase 1b/1c ---
    builder.add_node("checkpoint1", checkpoint1_node)
    builder.add_node("peers", peers_node)        # Phase 2 — ADR-015
    builder.add_node("checkpoint2", checkpoint2_node)

    # --- Nodos Phase 1c ---
    builder.add_node("valuation", valuation_agent)
    builder.add_node("value_expert", value_expert_agent)
    builder.add_node("critic", critic_agent)
    builder.add_node("archiver", archiver_node)

    # --- Edges Phase 1a: flujo lineal hasta financials ---
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", "financials")

    # --- Fan-out: financials → 4 especialistas en paralelo via Send() ---
    # add_conditional_edges con una función que devuelve list[Send] es el
    # patrón oficial de LangGraph para paralelismo dinámico (Phase 1b).
    builder.add_conditional_edges("financials", _route_to_specialists)

    # --- Fan-in: todos los especialistas convergen en checkpoint1 ---
    # LangGraph espera a que todos los nodos del fan-out terminen antes
    # de ejecutar checkpoint1. El merge de los 4 dicts es automático.
    for specialist in ("management", "filings", "market", "competitive"):
        builder.add_edge(specialist, "checkpoint1")

    # --- checkpoint1 → peers → checkpoint2 → valuation (Phase 1c) ---
    # peers_node fetcha XBRL de los peer tickers aportados por el usuario en
    # Checkpoint 1. Si el usuario hizo skip (peers=[]), el nodo devuelve []
    # inmediatamente sin llamar a EDGAR. ADR-015.
    builder.add_edge("checkpoint1", "peers")
    builder.add_edge("peers", "checkpoint2")
    builder.add_edge("checkpoint2", "valuation")

    # Phase 1c: valuation → value_expert → critic → archiver → END
    builder.add_edge("valuation", "value_expert")
    builder.add_edge("value_expert", "critic")
    builder.add_edge("critic", "archiver")
    builder.add_edge("archiver", END)

    return builder.compile(checkpointer=checkpointer)
