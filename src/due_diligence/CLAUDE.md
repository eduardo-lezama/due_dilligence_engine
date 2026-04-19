# CLAUDE.md — due_diligence package

Code conventions for `src/due_diligence/`. Complements root CLAUDE.md.

## Module structure
- `config.py` — Settings (pydantic-settings). Only place that reads env vars.
- `schemas/` — Pydantic v2 models. Zero business logic, only data shape.
- `llm/` — Provider wrapper. Only place that imports `anthropic` or `openai`.
- `tools/` — Deterministic data adapters. Never call LLM from here.
- `cli.py` — Typer entry points. Thin: calls tools/agents, formats with rich.

## Invariants
- All module outputs are Pydantic v2 models. No bare `dict` returns.
- `from due_diligence.llm.client import get_client` — never import anthropic/openai directly.
- `from due_diligence.config import settings` — never read env vars directly.
- Async functions use `async def` + `await`. Don't mix sync/async in the same call chain.
- structlog for all logging: `log = structlog.get_logger()`.

## Naming
- Adapters: `*Adapter` (e.g. `EdgarAdapter`).
- Providers: `*Provider` (interface) + `*Adapter` (implementation).
- Schemas: PascalCase Pydantic models in `schemas/base.py`.
- Tools (public API): `get_*`, `fetch_*` snake_case functions.
