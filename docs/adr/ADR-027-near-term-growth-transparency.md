# ADR-027: Near-Term Growth Transparency — 5-Layer Pattern for Critical DCF Parameters

**Status**: Accepted  
**Date**: 2026-05-16  
**Deciders**: Eduardo (AI/Agentic Engineering Lead)  
**Tags**: `valuation`, `dcf`, `transparency`, `schema`, `value-expert`, `EC-54`

## Context

`near_term_growth` is the most sensitive parameter in the DCF calculation — small changes (±2pp) produce large swings in intrinsic value. Before EC-54, this parameter was computed internally by `valuation_agent()` and was effectively invisible:

- The CLI prompted the user for an override without showing the auto-computed value or its source.
- The report footnote only showed `WACC` and `terminal_growth`, not the growth rate actually used.
- The Value Expert received the DCF output (intrinsic value) but had no information about how the growth assumption was derived — it could not assess whether the DCF was based on a 5-year historical FCF CAGR or a generic 7% fallback.
- `dcf_reliability_note` was only constructed in two narrow error conditions (FCF growth capped, or large spread between DCF upside and reverse-DCF implied growth). For the majority of analyses, it was `None`.

**Audit finding**: an Opus 4.7 external audit flagged that Value Expert commentary on §6 Valuation was shallow because it lacked the provenance of the growth rate. A DCF built on a 3-year FCF CAGR from a cyclical peak is very different from one built on a 10-year compounding average — but the LLM had no way to distinguish them.

**Root cause**: critical parameters in the tool-lane were computed but not surfaced. The schema had no fields for "which value did we use and where did it come from?", the CLI never showed computed values before prompting overrides, and the LLM-lane context was blind to the decision.

## Decision

Apply a **5-layer transparency pattern** to `near_term_growth`. This pattern is generalizable to any critical parameter where provenance matters.

### Layer 1 — Schema fields

Add two fields to `ValuationReport`:

```python
near_term_growth_used_pct: Optional[float] = None   # e.g. 0.123 = 12.3%
near_term_growth_source: Optional[str] = None       # see source hierarchy below
```

These fields are the authoritative machine-readable record of what was used and why.

### Layer 2 — Pure function extraction

Extract `_compute_near_term_growth()` from `valuation_agent()` as a pure function:

```python
def _compute_near_term_growth(
    financials: Optional[FinancialsReport],
    revenue_cagr: Optional[float],
    user_override_pct: Optional[float],
) -> tuple[float, str]:
```

**Why pure**: both `valuation_agent()` and `_ask_dcf_assumptions()` in the CLI need to compute the auto value — without extraction, either the CLI calls the agent (wrong coupling) or duplicates the logic (wrong DRY). A pure function with no side effects is the correct factoring.

**Source hierarchy** (most to least reliable):

| Source | Condition | Note |
|---|---|---|
| `user_override` | `user_override_pct is not None` | User explicitly overrode |
| `fcf_cagr_Ny` | FCF CAGR available, not above revenue CAGR | Historical compounding, N years |
| `fcf_cagr_Ny_rev_capped` | FCF CAGR > revenue CAGR | FCF cannot grow faster than revenue sustainably |
| `revenue_cagr_fallback` | No FCF CAGR, revenue CAGR available | Proxy — FCF track record unavailable |
| `default_7pct` | Neither FCF nor revenue CAGR | Generic fallback — no empirical basis |

### Layer 3 — CLI preview before override prompt

`_ask_dcf_assumptions()` calls `_compute_near_term_growth()` before displaying the prompt:

```
Auto near-term growth: 8.3% (fcf_cagr_3y)
Applied annually for 10 years (single-rate two-stage DCF).
```

For `default_7pct`: red warning. For `fcf_cagr_Ny_rev_capped`: yellow warning. This gives the user the information needed to decide whether to override — previously, the prompt appeared with no context.

### Layer 4 — Report footnote (archiver)

The DCF footnote in the archived Markdown report now includes:

```
*DCF assumptions: WACC=9.0%, near-term growth=8.3% (fcf_cagr_3y), terminal=2.5%, 10 years*
```

### Layer 5 — LLM context block + prompt rule

`_format_context()` in `value_expert.py` emits a `## DCF GROWTH ASSUMPTION` block before the peer valuation section:

```
## DCF GROWTH ASSUMPTION (use in §6 — most sensitive DCF parameter)
Near-term FCF growth: 8.3% (historical FCF CAGR, 3 years).
```

For `default_7pct` the block says "GENERIC FALLBACK — no empirical basis" with a CAUTION flag. Rule #20 in the system prompt requires the Value Expert to assess growth credibility in §6.

### dcf_reliability_note always built

Before EC-54, `dcf_reliability_note` was constructed only in two error conditions. After EC-54, it is always built with the growth context as the first entry. This ensures the note is always non-`None` when DCF runs, giving the archiver and the user a consistent point to read growth provenance.

### Bug fixed: archiver has_contradiction check

`archiver.py` checked `"contradiction" in valuation.dcf_reliability_note.lower()` to detect when to show the spread-note reading guide. The spread note text contains "spread note" not "contradiction" — the check was always `False`, the reading guide was never shown. Fixed to `"spread note" in valuation.dcf_reliability_note.lower()`.

## Architecture

```
_compute_near_term_growth(financials, revenue_cagr, user_override_pct)
    → (growth: float, source: str)
    ↑ called by both valuation_agent() and _ask_dcf_assumptions()

valuation_agent()
    → ValuationReport(
        near_term_growth_used_pct=...,
        near_term_growth_source=...,
        dcf_reliability_note="Near-term FCF growth: X% (...). ..."  ← always
      )

archiver()
    → footnote includes growth rate + source

value_expert _format_context()
    → ## DCF GROWTH ASSUMPTION block

CLI _ask_dcf_assumptions()
    → shows auto value before override prompt
```

## Consequences

### Positive
- **User decision quality**: override prompt shows the computed value and its source before asking. The user knows what they're overriding.
- **LLM reasoning quality**: Value Expert can now assess whether the DCF growth assumption is credible given §5 growth analysis — before EC-54 it was reasoning blindfolded.
- **Audit trail**: `near_term_growth_source` in schema means every report has a machine-readable record of which growth path was taken.
- **dcf_reliability_note always present**: consistent interface for consumers of the report.
- **Generalizable pattern**: the 5-layer pattern (schema + pure function + CLI preview + archiver footnote + LLM context) can be applied to any critical parameter (e.g., WACC derivation if automated in a future phase).

### Negative
- **Test surface**: 13 new tests across 3 test files for complete coverage.
- **Minor added context tokens**: the DCF GROWTH ASSUMPTION block adds ~100-200 tokens to Value Expert context per analysis.

### Not changed
- **Calculation logic unchanged**: the growth rate computation, clamping to `[_MIN_NEAR_TERM_GROWTH, _MAX_NEAR_TERM_GROWTH]`, and DCF math are identical to pre-EC-54. This is a transparency change, not a recalibration.

## Alternatives Considered

### A. Show growth in CLI output only (no schema fields)
**Rejected**: display-only transparency doesn't help the LLM (Value Expert) or create an audit trail in the report.

### B. LLM selects growth rate (not deterministic)
**Rejected**: violates ADR-005 (zero numerical hallucination). Growth rate must come from deterministic computation over primary data.

### C. Always use user override (force manual input)
**Rejected**: most analyses use auto-computed growth without override. Forcing a manual input would create friction without improving quality.

## Related

- **ADR-005**: Hallucination control — growth rate stays deterministic
- **ADR-022**: Small-cap display + DCF growth methodology (original decision to use `min(FCF_CAGR, revenue_CAGR)`)
- **EC-54**: Near-term growth transparency audit finding
- **Rule #20**: Value Expert prompt instruction for DCF growth reflexion
