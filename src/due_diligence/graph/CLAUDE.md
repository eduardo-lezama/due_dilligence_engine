# CLAUDE.md — graph/

LangGraph graph definition. Wires nodes, edges, and checkpointer.

## Rules
- `state.py` owns `AnalysisState` and nothing else. No business logic.
- `graph.py` owns `build_graph()`. It imports nodes from `agents/` — never implements logic here.
- Never import LangGraph internals outside this directory (agents/ can import state.py).
- `build_graph(checkpointer=None)` — always accept optional checkpointer so tests can pass MemorySaver.
- Thread ID for checkpointer = `run_id` from `AnalysisState`. Always set before invoking.

## Fan-out pattern (Phase 1b)
When adding parallel specialists, use `Send()` from a conditional edge on the supervisor node.
Each Send() passes a copy of the state to one specialist. LangGraph joins automatically.
