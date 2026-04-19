# CLAUDE.md — agents/

Specialist agent nodes for the LangGraph graph.

## Rules
- Each agent is a pure function: `def agent_node(state: AnalysisState) -> dict`.
- Return only the keys you modify — never return the full state.
- Tool-lane agents (Financials, Market): call adapters from `tools/`, no LLM.
- LLM-lane agents (Mgmt, Filings, Competitive): use `get_client()` from `llm/`, emit Citations.
- Every Citation must pass substring-match validation before being included in the return dict.
- Tests MUST mock all external calls (EDGAR, Polygon, Finnhub, LLM). Zero real API calls in pytest.
- When a stub is replaced by a real implementation, flip `stub=False` in its Pydantic output.

## Node signature pattern
```python
def specialist_node(state: AnalysisState) -> dict:
    ticker = state["ticker"]
    report = ...
    return {"specialist_field": report}
```

## Adding a new specialist (Phase 1b+)
1. Create `agents/<name>.py` with `<name>_node(state) -> dict`.
2. Update the Pydantic output schema in `schemas/reports.py` (set stub=False, fill real fields).
3. Register node + Send() edge in `graph/graph.py`.
4. Add fixture in `tests/fixtures/`.
5. Write test in `tests/test_<name>_agent.py`.
