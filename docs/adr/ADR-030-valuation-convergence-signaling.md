# ADR-030: Valuation Methods Convergence Signaling

**Status**: Accepted  
**Date**: 2026-05-17  
**Deciders**: Eduardo (AI/Agentic Engineering Lead)  
**Tags**: `valuation`, `convergence`, `epv`, `dcf`, `reverse-dcf`, `peer-multiples`, `value-expert`

## Context

After adding EPV and 3-stage DCF (ADR-029), the system had four distinct valuation signals:

1. **DCF upside**: forward-looking, growth-dependent
2. **EPV upside**: zero-growth anchor, quality business test
3. **Reverse DCF vs historical CAGR**: implied growth vs what the company has historically delivered
4. **EV/EBIT vs peer median**: relative valuation, multiple compression/expansion signal

The problem: these signals existed in isolation. The Value Expert received the raw numbers but had no structured guide to synthesize them. An analyst reading the report would naturally ask "do these methods agree?" — but the LLM had to figure out the convergence question on its own each time, producing inconsistent quality in §6.

**Audit finding (Opus 4.7)**: §6 commentary was shallow because the Value Expert had no structured convergence framework. When three methods agreed and one dissented, the LLM sometimes ignored the dissent, sometimes anchored on it disproportionately.

## Decision

### 1. Four Classifiers, Each Deterministic

Each method produces a simple 3-level classification: `cheap` / `fair` / `expensive`. These are computed in Python, not by the LLM.

| Method | Classification logic |
|--------|---------------------|
| DCF upside | cheap: ≥20%; fair: 0–20%; expensive: <0% |
| EPV upside | cheap: ≥10%; fair: -10–10%; expensive: <-10% |
| Reverse DCF vs historical | cheap: implied < historical × 0.75; fair: within 25% of historical; expensive: implied > historical × 1.25 |
| EV/EBIT vs peer median | cheap: discount ≥ 25% to peer; fair: within 25%; expensive: premium ≥ 25% |

Thresholds are intentionally coarse. The goal is not precision — it is directional agreement. Ambiguous cases are `fair`, not forced into cheap/expensive.

### 2. `_build_methods_summary` Returns a Structured List

```python
_build_methods_summary(
    dcf_upside_pct, epv_upside_pct,
    reverse_implied_growth, historical_fcf_cagr,
    peer_ev_ebit_median, ev_ebit_current
) → list[dict]
```

Each dict: `{"method": str, "per_share": str, "signal": str, "classification": str}`.

N/A entries are included when data is missing — the absence of a signal is itself informative (e.g., if peer data is unavailable, EV/EBIT comparison cannot confirm cheap thesis).

Stored in `ValuationReport.valuation_methods_summary` for archiving.

### 3. Convergence Pushed to Value Expert Lane (Rule #21)

**The system does NOT automatically derive a convergence verdict.** This is intentional.

Convergence has signal only in context:
- Three methods say cheap but EPV says expensive → high-growth premium, not a value trap if ROIC is high
- All four say cheap → highest conviction
- DCF cheap but EPV expensive → growth story, requires sustained execution
- Reverse DCF says cheap but peer EV/EBIT says expensive → sector rotation story

The LLM can reason about these patterns. A hard rule (e.g., "3/4 cheap = BUY") would ignore ROIC, debt, moat durability, and management quality that the other agents supply.

**Rule #21** was added to the Value Expert system prompt:

> In §6 VALUATION, explicitly synthesize the VALUATION METHODS CONVERGENCE block. State which methods agree, which diverge, and what the divergence implies. A convergence count (e.g. "3/4 cheap") must appear in §6. Do not bury this in footnotes.

### 4. Archiver Renders the Convergence Table

The archiver renders `valuation_methods_summary` as a Markdown table in the stored report under `### Valuation Methods Convergence`:

```
| Method | Per Share | Signal | Classification |
| DCF (3-stage) | $XX.XX | +XX% upside | cheap |
| EPV (Greenwald) | $XX.XX | +XX% upside | cheap |
| Reverse DCF | — | implied X% vs hist Y% | fair |
| EV/EBIT vs Peers | — | X× vs Y× peers | expensive |

3/4 methods classify as cheap or fair.
```

This is the same information passed to the Value Expert context, rendered for human inspection.

### 5. Value Expert Context Block

The Value Expert receives the convergence block as structured text before its synthesis section, including a BUY signal line when EPV > market cap (highest-conviction case):

```
## VALUATION METHODS CONVERGENCE
| Method | Per Share | Signal | Classification |
...
Convergence: 3/4 cheap
> EPV > market_cap: no-growth intrinsic value exceeds current price
```

## Architecture

```
_classify_dcf_upside(upside_pct) → str
_classify_epv_upside(upside_pct) → str
_classify_reverse_vs_historical(implied, historical) → str
_classify_ev_ebit_vs_peers(ev_ebit, peer_median) → str
    ↑ all called by _build_methods_summary()

_build_methods_summary(...) → list[dict]
    ↑ called by valuation_agent()
    ↑ stored in ValuationReport.valuation_methods_summary

archiver.py: renders convergence table from methods_summary
value_expert.py: receives convergence block in _format_context()
value_expert.py: Rule #21 requires explicit synthesis in §6
```

## Consequences

### Positive
- **§6 quality**: Value Expert now has a convergence scaffold. The LLM's job is synthesis, not data extraction.
- **Human-readable audit**: the convergence table in the archived report lets Eduardo check the LLM's reasoning against the raw classifications.
- **Flexible thresholds**: classifiers are simple functions — thresholds can be tuned without touching schema or prompt.
- **Missing data is explicit**: N/A entries in the table force the LLM to acknowledge gaps rather than silently skipping methods.

### Negative
- **Coarse thresholds**: ±25% and ±20% are heuristics, not statistically calibrated. For companies with unusually high or low multiples, a `fair` classification may mask a meaningful signal.
- **Peer data dependency**: EV/EBIT classification requires user-supplied peers. When peers are absent (no peer run), this method is N/A and convergence is 3-method at best.
- **Rule #21 enforcement**: enforced by prompt instruction, not code. A future model regression could ignore the rule. Mitigation: the convergence block is prominent in context and the rule is explicit.

## Alternatives Considered

### A. Hard convergence verdict in code
**Rejected**: "3/4 cheap = BUY" ignores ROIC, leverage, moat, management quality. The system is a decision-support tool, not an oracle. The LLM synthesis is the correct layer for this judgment.

### B. Single composite valuation score
**Rejected**: aggregating four methods into one number destroys information. An analyst needs to know *which* methods agree and *why* one dissents — not a weighted average.

### C. Skip EV/EBIT (peer data often absent)
**Rejected**: when peer data is present, this is the most objective method (market-derived multiples, no growth assumptions). N/A is acceptable. Removing the method entirely would reduce coverage for companies where peers are supplied.

## Related

- **ADR-029**: 3-stage DCF + EPV + WACC advisory (upstream of this ADR)
- **ADR-027**: Near-term growth transparency (5-layer pattern this ADR follows)
- **ADR-009**: Value Expert structured analysis + verdict
- **ADR-015**: Peer comparison user-supplied at Checkpoint 1
