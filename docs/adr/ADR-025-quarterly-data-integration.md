# ADR-025: Quarterly Data Integration Strategy

**Status**: Accepted  
**Date**: 2026-05-15  
**Deciders**: Eduardo (AI/Agentic Engineering Lead)  
**Tags**: `data-sourcing`, `quarterly-reporting`, `staleness`, `EC-44`, `EC-46`

## Context

The system originally relied solely on annual 10-K data for financial analysis. This created a critical staleness problem: when analyzing a company 6+ months after its fiscal year-end, the 10-K data was outdated and could miss material quarterly deterioration (e.g., MRCY's Q1 2026 margin compression wasn't visible in the FY2025 10-K).

**Problem manifestations:**
- Value Expert analyzing stale data with excessive confidence
- Missing recent guidance cuts, margin compression, or revenue declines
- Contradictory verdicts (e.g., "HOLD with thesis-breaker already triggered")
- No integration of quarterly earnings metrics into §4 Financial Quality

**Root cause:** Annual reporting cadence (10-K) vs quarterly business reality (earnings calls every 90 days).

## Decision

Implement a **dual-source quarterly data strategy**:

### 1. Primary: 8-K Item 2.02 Earnings Press Release (EC-44)
- **What**: Full-text extraction of earnings press releases filed as 8-K Item 2.02
- **When**: Fetched in `market_agent` for all tickers
- **Coverage**: Revenue, EPS, guidance, operational metrics (backlog, margins, etc.)
- **Advantage**: Narrative context + hard numbers in one document
- **Implementation**: `EdgarEventsAdapter.fetch_earnings_press_release()`
- **Surfaced as**: "QUARTERLY UPDATE" section in Value Expert context

### 2. Secondary: 10-Q XBRL Snapshot (MCE-24 light)
- **What**: Deterministic XBRL extraction of 3 key metrics from most recent 10-Q
- **Metrics**: Revenue, Operating Income, Gross Profit (quarterly, not YTD)
- **When**: Fetched in `financials_node` only if 10-K is >90 days stale
- **Advantage**: GAAP-compliant, deterministic, zero hallucination risk
- **Implementation**: `EdgarAdapter.fetch_quarterly_snapshot()`
- **Surfaced as**: "QUARTERLY XBRL" section in Value Expert context

### 3. Staleness Warning (EC-46)
- **What**: Explicit warning when 10-K data is >180 days old
- **Where**: Injected at top of FINANCIALS section in Value Expert context
- **Purpose**: Instructs LLM to prioritize quarterly data over annual data for §4/§5/§7

## Architecture

```
financials_node (Phase 1a)
  ├─ EdgarAdapter.fetch_raw()           → 10-K annual XBRL
  └─ EdgarAdapter.fetch_quarterly_snapshot()  → 10-Q XBRL (if stale)
       ↓
  state["quarterly_snapshot"] = {period, revenue, operating_income, gross_profit}

market_agent (Phase 1b)
  └─ EdgarEventsAdapter.fetch_earnings_press_release()  → 8-K Item 2.02
       ↓
  state["market"].quarterly_earnings_text = "..."
  state["market"].quarterly_earnings_date = "2026-05-01"

value_expert_agent (Phase 1c)
  └─ _format_context()
       ├─ if financials.as_of_date > 180 days → ⚠️ STALENESS WARNING
       ├─ if quarterly_snapshot → ## QUARTERLY XBRL section
       └─ if quarterly_earnings_text → ## QUARTERLY UPDATE section
```

## Consequences

### Positive
- **Recency**: Value Expert now sees data <90 days old for most companies
- **Accuracy**: Catches quarterly deterioration that annual reports miss
- **Determinism**: 10-Q XBRL maintains zero-hallucination guarantee (ADR-005)
- **Fail-loud**: If 10-Q unavailable, system continues with 10-K + warning
- **Minimal latency**: 10-Q fetch only when needed (staleness gate)

### Negative
- **Complexity**: Two quarterly data sources to maintain
- **API calls**: +1 EDGAR request per analysis when 10-K is stale
- **Coverage gaps**: Not all companies file 8-K Item 2.02 (some use 8-K Item 7.01 or skip)
- **Parsing brittleness**: Press releases are unstructured; LLM must extract metrics

### Mitigations
- Press release extraction uses `obj[item_number]` API (edgartools 5.x) — more reliable than scraping
- 10-Q XBRL uses same deterministic pipeline as 10-K (shared `_get_val` logic)
- Staleness threshold (90 days) is configurable via `staleness_threshold_days` parameter
- System degrades gracefully: if both sources fail, analysis continues with 10-K + warning

## Alternatives Considered

### A. Full 10-Q XBRL Parser (MCE-24 complete)
- **Rejected**: Over-engineering for Phase 2. 3 metrics (revenue/EBIT/GP) cover 80% of use cases.
- **Deferred to**: Phase 3+ when margin trend analysis becomes critical

### B. Finnhub Quarterly Financials API
- **Rejected**: Third-party aggregator violates ADR-011 (primary-source-first)
- **Risk**: Data quality/timeliness not guaranteed; adds vendor dependency

### C. Earnings Call Transcripts Only
- **Rejected**: Transcripts are qualitative; don't replace hard GAAP numbers
- **Note**: Transcripts remain valuable for guidance/tone (ADR-016) but aren't a substitute for XBRL

## Related

- **ADR-005**: Hallucination Control by Architecture — quarterly XBRL maintains determinism
- **ADR-011**: US Data Sources — 8-K and 10-Q are primary SEC sources
- **ADR-017**: 8-K Event Tracking — extends to Item 2.02 press releases
- **EC-44**: MRCY audit finding (margin compression missed)
- **EC-46**: Staleness warning requirement
- **MCE-24**: Quarterly margin trends (full 10-Q parser deferred)

## Implementation Notes

- `MarketReport` schema extended with `quarterly_earnings_text` and `quarterly_earnings_date`
- `AnalysisState` extended with `quarterly_snapshot: NotRequired[dict]`
- Value Expert Rule #13 updated to mandate quarterly data integration into §4/§5/§7
- Tests: 254 passing (24 new tests for quarterly data surfacing and staleness warnings)
