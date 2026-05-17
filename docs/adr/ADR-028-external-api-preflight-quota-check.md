# ADR-028: External API Pre-Flight Quota Check

**Status**: Accepted  
**Date**: 2026-05-17  
**Deciders**: Eduardo (AI/Agentic Engineering Lead)  
**Tags**: `supervisor`, `tavily`, `reliability`, `graceful-degradation`, `quota`

## Context

Tavily has a monthly credit limit (free tier: 1000 credits/month). Each full DD analysis consumes 9-14 Tavily credits:

| Scenario | Credits |
|---|---|
| Normal (Finnhub transcripts available, no drawdown) | 9 |
| No Finnhub transcripts (Tavily fallback) | 11 |
| Drawdown detected | 12 |
| Worst case (no transcripts + drawdown) | 14 |

When the monthly limit is exhausted, Tavily search calls fail silently (the adapter returns `[]` and the agents continue with degraded web context). This is the correct behavior for individual search failures, but it's the wrong behavior when the **entire quota** is exhausted:

- EDGAR, Polygon, Finnhub, and LLM calls continue, consuming time and money.
- The analysis completes but is structurally degraded: Management and Competitive agents have no web research context — two of their core data sources.
- The user discovers the problem only at the end, after spending ~2-4 minutes and API costs.

**User's workflow**: Eduardo has a backup Tavily API key in `.env`. When the primary account is exhausted, the fix is a manual key swap. The system should detect this condition early and fail loud with actionable instructions.

## Decision

Implement a **pre-flight quota check** as the first action in `supervisor_node`, before any other API call (EDGAR, Polygon, Finnhub, or LLM).

### Interface

```python
# tools/tavily.py
class TavilyQuotaExceededError(Exception):
    """Raised when Tavily monthly quota is nearly exhausted (< buffer credits remaining)."""
    pass

def check_tavily_quota(buffer: int = 40) -> None:
    """
    Calls GET /usage endpoint. Raises TavilyQuotaExceededError if
    credits_used >= credits_limit - buffer.

    No-op if TAVILY_API_KEY is not configured.
    Fails gracefully (log warning, continue) if /usage endpoint is unreachable.
    """
```

### Buffer rationale

`buffer=40` means: stop when fewer than 40 credits remain. At 9-14 credits per analysis, this leaves 2-3 complete analyses before the actual limit. This avoids a race condition where the check passes but the analysis fails mid-run.

### Placement in supervisor_node

```python
def supervisor_node(state):
    ticker = state["ticker"].upper()

    # Pre-flight: check Tavily quota before consuming any other API
    from due_diligence.tools.tavily import check_tavily_quota
    check_tavily_quota()   # raises TavilyQuotaExceededError if nearly exhausted

    set_identity(settings.sec_user_agent)
    # ... EDGAR resolution ...
```

**Why supervisor**: it is the first node in the graph. Nothing else has run. No resources have been consumed. Stopping here is free.

### Error propagation

`TavilyQuotaExceededError` propagates up through `graph.invoke()` to the CLI, which catches it:

```python
try:
    graph.invoke({"ticker": ticker}, config=config)
except TavilyQuotaExceededError as e:
    console.print("[bold red]Tavily quota exhausted — analysis aborted.[/bold red]")
    console.print(f"[red]{e}[/red]")
    raise typer.Exit(1)
```

The message includes the current credit count and explicit instructions: "Switch TAVILY_API_KEY in .env to your backup account and restart."

### Implementation details

- Uses `urllib.request` (Python stdlib) — no new dependency.
- Timeout: 5 seconds. Network errors caught and logged as warnings; analysis continues.
- Response parsing is defensive: handles both `{"usage": {"credits_used": N}}` and flat `{"credits_used": N}` shapes.
- If `TAVILY_API_KEY` is not set, the check is a no-op (Tavily will simply not be called during the analysis).

### Test fixture pattern

The `check_tavily_quota()` function makes a real HTTP call. All tests must mock it to avoid:
1. Real HTTP calls in CI/CD
2. Tests failing when `TAVILY_API_KEY` is not set in the test environment

**Pattern**: add an `autouse=True` fixture in `conftest.py`:

```python
@pytest.fixture(autouse=True)
def mock_tavily_quota_check():
    with unittest.mock.patch("due_diligence.tools.tavily.check_tavily_quota"):
        yield
```

Tests that verify the quota behavior override this fixture with `mocker.patch(...)` or use a module-level reference to the real function captured before the autouse applies:

```python
# At module level, before autouse runs:
from due_diligence.tools.tavily import check_tavily_quota as _real_check_tavily_quota
```

This pattern is safe because `from module import func` captures the function object reference, while the autouse replaces the module attribute — the captured reference still points to the real function.

## Consequences

### Positive
- **Fast fail**: analysis aborts in < 1 second instead of running for 2-4 minutes before failing.
- **Zero resource waste**: EDGAR, Polygon, Finnhub, and LLM are never called if Tavily is exhausted.
- **Actionable message**: the error tells the user exactly what to do (swap the key in `.env`).
- **Graceful network failure**: if `/usage` is unreachable, the check is a no-op — analysis continues. Conservative: prioritizes availability over strict quota enforcement.

### Negative
- **Extra HTTP call per analysis**: ~50ms to call `/usage` before each run. Acceptable.
- **False negative possible**: if `/usage` is unreachable, the quota check is bypassed and the analysis may fail mid-run. Mitigated by: (a) Tavily's uptime is high, (b) individual search errors are already handled gracefully.
- **Buffer is a heuristic**: `buffer=40` means 2-3 analyses are "reserved". This is intentional — better to prompt the key swap early than to fail mid-run.

## Alternatives Considered

### A. Catch-and-fail at search time
**Rejected**: individual search failures already fail gracefully (return `[]`). A quota-exhausted failure looks identical to a normal "no results" case — the degradation is silent and undetectable without the pre-flight check.

### B. Expose remaining credits in the CLI at startup
**Rejected**: "you have 45 credits left" is noise for normal runs. The pre-flight check only speaks when there's a problem.

### C. Automatic key rotation
**Rejected**: `.env` contains credentials managed by the user. Automatic rotation would require storing multiple keys in config, adding complexity. Eduardo's workflow (manual swap) is simple and correct for a personal tool.

### D. Hard stop at credits_limit (no buffer)
**Rejected**: race condition. Multiple concurrent invocations (or a run started just before the check) could exhaust the remaining credits. Buffer ensures the check is conservative.

## Related

- **ADR-012**: Management Agent data sources — Tavily is a primary source for management web research
- **ADR-003**: Supervisor as entry point — the supervisor is the correct place for pre-flight checks
- **ADR-005**: Fail loud — silent degradation of a primary data source violates the fail-loud principle
