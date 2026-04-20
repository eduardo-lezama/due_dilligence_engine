# Phase 1c Implementation — Valuation + Value Expert + Critic + Archiver

> **Note:** This is a retrospective document. Phase 1c was implemented directly in session without a pre-written plan.

**Goal:** Complete the analysis pipeline with deterministic valuation calculations, LLM synthesis (VERDICT), red-team critic, and persistence.

**Architecture:** Sequential after checkpoint2: valuation (tool-lane) → value_expert (LLM) → critic (LLM) → archiver (I/O). Two HITL checkpoints.

**Tech Stack:** LangGraph Command(resume=), gpt-5.4-mini (value_expert + critic), gpt-4.1 (other agents), SQLite, Rich CLI

---

## What was built

### Config changes

- [x] `src/due_diligence/config.py` — `openai_fallback_model` upgraded from `gpt-4o-mini` to `gpt-4.1`; new `openai_expert_model=gpt-5.4-mini`
- [x] `src/due_diligence/llm/client.py` — `_complete_openai()` now accepts `model` param; callers can specify OpenAI model explicitly

### Schema changes

- [x] `src/due_diligence/schemas/reports.py` — `ValuationReport` extended with `ev_ebit_multiple`, `ev_fcf_multiple`, `earnings_yield_pct`, `dcf_upside_pct`

### New agents

- [x] `src/due_diligence/agents/checkpoint.py` — added `checkpoint2_node` (DCF assumptions HITL)
- [x] `src/due_diligence/agents/valuation.py` — tool-lane: 2-stage DCF, reverse DCF (binary search), multiples, sensitivity table 3×3
- [x] `src/due_diligence/agents/value_expert.py` — LLM synthesis: 9 sections + VERDICT (BUY/HOLD/SELL), uses openai_expert_model
- [x] `src/due_diligence/agents/critic.py` — LLM red-team: weaknesses + contra-thesis + pre-mortem, uses openai_expert_model
- [x] `src/due_diligence/agents/archiver.py` — persists markdown to data/reports/ + row to data/analyses.db

### Graph wiring

- [x] `src/due_diligence/graph/graph.py` — full Phase 1c chain: checkpoint1 → checkpoint2 → valuation → value_expert → critic → archiver → END

### CLI

- [x] `src/due_diligence/cli.py` — rewritten `analyze` command with checkpoint loop: invoke → show checkpoint1 → confirm → invoke(Command) → ask DCF assumptions → invoke(Command) → show final report

### Tests

- [x] `tests/test_valuation.py` — 28 tests: DCF arithmetic, reverse DCF round-trip, sensitivity table, degraded modes
- [x] `tests/test_value_expert.py` — 17 tests: LLM mock, VERDICT validation, confidence clamping, _format_context
- [x] `tests/test_critic.py` — 16 tests: LLM mock, weaknesses parsing, prompt contents
- [x] `tests/test_archiver.py` — 14 tests: markdown rendering, SQLite insertion, degraded mode, idempotent _ensure_db

## Key decisions

- `valuation_agent` projects FCF directly (not revenue → margin → FCF) — simpler, fewer error sources
- `value_expert` uses `gpt-5.4-mini` (not gpt-4.1) — synthesis quality matters more here
- `critic` does NOT see Expert's full reasoning — only VERDICT + raw data — to avoid anchoring
- `archiver` returns `{}` — pure I/O side effect, does not modify graph state
- `magic_formula_rank=None` always — ranking requires peer list, deferred to Phase 2
- CLI reuses same `thread_id` per ticker (`{TICKER}-latest`) — allows resuming interrupted runs

## Total tests after Phase 1c

152 passing, 0 failures, 0 real API calls.

## Graph final structure (12 nodes)

```
START → supervisor → financials
      → [Send × 4] → management
                    → filings
                    → market
                    → competitive
      → checkpoint1 (HITL: review specialists)
      → checkpoint2 (HITL: DCF assumptions)
      → valuation
      → value_expert
      → critic
      → archiver
      → END
```
