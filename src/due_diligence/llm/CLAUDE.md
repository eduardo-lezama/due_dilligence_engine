# CLAUDE.md — llm/

Provider wrapper. The only module that imports `anthropic` or `openai`.

## Rules
- `client.py` exports `get_client() -> LLMClient` and `LLMClient.complete()`.
- Never raise a provider-specific exception outside this module — catch and re-raise as `LLMError`.
- Fallback order: Claude primary → OpenAI. Log the fallback with structlog at WARNING.
- Model selection: always accept an explicit `model` param; fall back to `settings.default_model`.
- Prompt caching (Anthropic): use `cache_control` blocks when system prompt > 1024 tokens. Mark the checklist and verdict rubric as cacheable.
- Cost tracking: log `input_tokens`, `output_tokens`, `model` on every call at DEBUG level.

## Do not add here
- Business logic (that belongs in agents/).
- Retry logic beyond 2 attempts + fallback (YAGNI).
- Streaming (deferred until Phase 1 CLI needs it).
