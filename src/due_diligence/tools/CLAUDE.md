# CLAUDE.md — tools/

Deterministic data adapters. Zero LLM calls. Numbers only from here.

## Rules
- Every adapter implements a `fetch_raw(ticker) -> dict` (network call) + `parse_*(raw) -> PydanticModel` (pure function, testable with fixtures).
- Tests MUST mock `fetch_raw` or use fixture files in `tests/fixtures/`. Zero real API calls in pytest.
- Fail loud: if a required metric is missing from source, raise `DataUnavailableError` with ticker + field. Never return `None` silently for a required metric.
- Always set `currency_code` on MetricValue. Never assume USD without explicit XBRL tag.
- Cache raw API responses in `tests/fixtures/<ticker>_<source>_raw.json` when running integration tests (mark with `@pytest.mark.integration`).

## Adapters planned
- `edgar.py` — SEC EDGAR XBRL via edgartools (primary, Phase 0-1).
- `polygon.py` — OHLCV prices via polygon-api-client (Phase 1).
- `finnhub.py` — earnings call transcripts via finnhub-python (Phase 1).
