"""
Tests para el esqueleto del grafo LangGraph (Phase 1a).

Qué verifican:
- build_graph() devuelve un grafo compilado con método invoke.
- El grafo acepta un checkpointer externo (necesario para tests con MemorySaver).
- El grafo acepta None como checkpointer (modo sin persistencia).

Nota: los tests de invocación end-to-end (con datos reales) viven en Task 7.
Aquí solo verificamos que el grafo se construye correctamente.
"""


def test_build_graph_returns_compiled_graph():
    """build_graph() con MemorySaver devuelve un CompiledGraph invocable."""
    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    graph = build_graph(checkpointer=MemorySaver())
    # CompiledGraph expone .invoke() — verificamos que el objeto es correcto
    assert hasattr(graph, "invoke")


def test_build_graph_without_checkpointer():
    """build_graph() sin checkpointer también devuelve un grafo válido."""
    from due_diligence.graph.graph import build_graph

    graph = build_graph()
    assert hasattr(graph, "invoke")
