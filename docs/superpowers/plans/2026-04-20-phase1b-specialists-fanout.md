# Phase 1b Implementation — Specialists + Fan-out + HITL

> **Note:** This is a retrospective document. Phase 1b was implemented directly in session without a pre-written plan. This file captures the decisions made and serves as documentation for future reference.

**Goal:** Add 4 specialist agents running in parallel via LangGraph fan-out, plus HITL checkpoint between Phase 1b and Phase 1c.

**Architecture:** Fan-out with `Send()` from `financials` node to 4 parallel specialists. LangGraph auto-merges results. `checkpoint1_node` uses `interrupt()` to pause before Phase 1c.

**Tech Stack:** LangGraph Send/interrupt, Tavily, Polygon.io, Finnhub, edgartools 5.x, Pydantic v2, gpt-4.1 (via OpenAI fallback)

---

## What was built

### New tools

- [x] `src/due_diligence/tools/tavily.py` — TavilyAdapter + format_tavily_for_prompt, degraded mode
- [x] `src/due_diligence/tools/polygon.py` — PolygonAdapter.fetch_market_data (price, 52w range, market cap)
- [x] `src/due_diligence/tools/finnhub.py` — FinnhubAdapter.fetch_company_news

### Extended tools

- [x] `src/due_diligence/tools/edgar.py` — EdgarFilingsAdapter (10-K §1/1A/7), EdgarManagementAdapter (DEF14A, Form4, §10), ROIC calculation in parse_financials
- [x] `src/due_diligence/schemas/base.py` — added roic_series, invested_capital, stockholders_equity to FinancialsReport

### New agents

- [x] `src/due_diligence/agents/management.py` — LLM-lane, §3 checklist (ownership, compensation, capital allocation)
- [x] `src/due_diligence/agents/filings.py` — LLM-lane, §1+§7 checklist (business, risks, MDA)
- [x] `src/due_diligence/agents/market.py` — tool-lane (no LLM), Polygon + Finnhub
- [x] `src/due_diligence/agents/competitive.py` — LLM-lane, §2 checklist (moat), reads state["financials"]
- [x] `src/due_diligence/agents/checkpoint.py` — checkpoint1_node with interrupt()

### Graph wiring

- [x] `src/due_diligence/graph/graph.py` — fan-out via `_route_to_specialists()` returning `list[Send]`, all 4 specialists converge at checkpoint1

### Tests

- [x] `tests/test_agents_1b.py` — 35 tests covering all 4 agents + adapter degraded modes
- [x] `tests/test_graph_1b.py` — 7 tests: graph structure, routing, checkpoint behavior, end-to-end with MemorySaver
- [x] `tests/fixtures/aapl_xbrl_raw.json` — updated to FY2025

## Key decisions

- `peer_roic_comparison` deferred to Phase 1c+ (ADR-005: no hallucination without XBRL peer data)
- `interrupt()` with MemorySaver: `invoke()` returns normally, graph paused; verify with `get_state().next`
- ROIC approximation: uses latest-year IC for all years in series (documented limitation)
- AAPL FY2025 ROIC ~87%: correct due to asset-light model + massive buybacks reducing book equity
