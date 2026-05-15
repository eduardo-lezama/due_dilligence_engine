# ADR-026: Thesis-Break Self-Consistency Validation

**Status**: Accepted  
**Date**: 2026-05-15  
**Deciders**: Eduardo (AI/Agentic Engineering Lead)  
**Tags**: `value-expert`, `validation`, `logical-consistency`, `EC-45`

## Context

The Value Expert's §9 Decision Frame requires defining a concrete, observable "thesis-breaker" — a condition that would invalidate the investment thesis. This is a critical risk management tool.

**Problem discovered in MRCY audit (EC-45):**
- Value Expert issued verdict: "HOLD — wait for margin stabilization"
- Thesis-breaker defined: "Thesis breaks if gross margin declines below 30%"
- **Contradiction**: §4 Financial Quality already stated "gross margin compressed to 28%"
- **Root cause**: LLM didn't cross-check its own thesis-breaker against the data it just analyzed

This is a **logical consistency failure**, not a data availability issue. The LLM had all the information but failed to verify self-consistency before finalizing the verdict.

## Decision

Implement a **two-layer thesis-break validation framework**:

### Layer 1: LLM Self-Check (Prompt-Level)
**Rule #15** added to Value Expert system prompt:

> THESIS-BREAK SELF-CHECK (§9): BEFORE finalizing the verdict, verify that the thesis-breaker condition you define in §9 is NOT ALREADY TRUE in the data you just analyzed. If the condition IS already met (e.g., you state "thesis breaks if gross margin falls below 30%" but §4 shows gross margin is already 28%), you MUST either (a) revise the verdict to reflect the already-broken thesis, or (b) choose a DIFFERENT thesis-breaker that is forward-looking.

**Rationale**: Instructs the LLM to perform the check as part of its reasoning process.

### Layer 2: Deterministic Post-Processing (Defense-in-Depth)
**Function**: `_thesis_break_self_check(report, context) -> ValueExpertReport`

**Algorithm**:
1. Extract negative condition phrases from `decision_frame` (e.g., "margin decline", "revenue fall", "guidance cut")
2. Check if those same phrases appear in `financial_quality`, `risks`, or `growth_reinvestment` sections
3. If overlap detected (≥2 words match), append warning caveat to `decision_frame`
4. Log `thesis_break_already_true` event with triggered phrases

**Heuristic nature**: This is a keyword-based detector, not semantic analysis. It will have false positives (e.g., "margin decline risk" vs "margin declining") and false negatives (paraphrased conditions). But it catches obvious contradictions like MRCY.

**Non-invasive**: Does NOT modify the verdict — only flags the inconsistency for Critic and user review.

## Architecture

```
value_expert_agent()
  ├─ LLM generates response with §9 Decision Frame
  ├─ _parse_response() → ValueExpertReport
  ├─ _thesis_break_self_check(report, context)  ← NEW
  │    ├─ if contradiction detected:
  │    │    ├─ log.warning("thesis_break_already_true", ...)
  │    │    └─ append caveat to decision_frame
  │    └─ return report (possibly modified)
  └─ return {"synthesis": report}
```

**Caveat format**:
```
⚠️ THESIS-BREAK SELF-CHECK WARNING: the conditions [margin decline, revenue fall] 
appear to be ALREADY TRUE based on the analysis above. This may indicate the thesis 
is already broken and the verdict should be reassessed.
```

## Consequences

### Positive
- **Catches logical contradictions** that Rule #15 alone might miss (LLM reasoning errors)
- **Fail-loud**: Flags inconsistencies explicitly rather than silently accepting them
- **User visibility**: Caveat appears in final report markdown for manual review
- **Critic integration**: Critic agent can detect the warning and challenge the verdict
- **Zero false sense of security**: If LLM defines a broken thesis-breaker, user knows immediately

### Negative
- **Heuristic limitations**: Keyword matching can miss paraphrased contradictions
- **False positives**: "margin risk" vs "margin declining" may trigger incorrectly
- **Maintenance burden**: Negative condition phrase list needs periodic updates
- **Not a substitute for LLM reasoning**: Layer 2 is defense-in-depth, not primary validation

### Mitigations
- Rule #15 is the primary mechanism — Layer 2 is backup
- Phrase list covers common value investing thesis-breakers (margins, revenue, guidance, backlog, debt, FCF)
- Warning is non-blocking — analysis completes even if triggered
- Tests verify both true positives (MRCY-like cases) and false negatives (forward-looking thesis-breakers)

## Alternatives Considered

### A. Semantic Similarity Check (Embeddings)
- **Rejected**: Over-engineering. Keyword overlap is sufficient for obvious contradictions.
- **Cost**: Adds embedding API calls and complexity.
- **Deferred to**: Phase 3+ if heuristic proves insufficient.

### B. LLM-Only Validation (No Layer 2)
- **Rejected**: Rule #15 alone is not defense-in-depth.
- **Risk**: LLM reasoning errors (attention failures, context window limits) can bypass prompt instructions.
- **Evidence**: MRCY case occurred despite clear data availability.

### C. Blocking Validation (Reject Report if Contradiction)
- **Rejected**: Too aggressive. Some contradictions may be intentional (e.g., "thesis breaks if condition worsens further").
- **Better**: Flag and let Critic + user decide.

## Related

- **ADR-009**: Verdict Plus Analysis — thesis-breaker is part of §9 Decision Frame
- **ADR-019**: Value Expert Prompt v2 — Rule #15 extends the prompt framework
- **EC-45**: MRCY thesis-break contradiction audit finding
- **Rule #15**: Thesis-break self-check prompt instruction

## Implementation Notes

- `_thesis_break_self_check()` runs after `_parse_response()` in `value_expert_agent()`
- Negative condition phrases: margin decline/compress/fall, revenue decline/drop/fall, guidance cut/withdraw/lower, backlog decline/shrink, debt increase/exceed, loss widen, negative FCF, cash burn
- Tests: 4 new tests covering margin decline, guidance cut, forward-looking (no false positive), empty sections (no crash)
- Warning logged to structlog with `ticker`, `triggered_phrases`, `verdict` for observability
