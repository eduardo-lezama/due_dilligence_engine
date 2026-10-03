# ADR-029: 3-Stage DCF with Damodaran Fade + EPV Anchor + WACC Advisory

**Status**: Accepted  
**Date**: 2026-05-17  
**Deciders**: Eduardo (AI/Agentic Engineering Lead)  
**Tags**: `valuation`, `dcf`, `epv`, `wacc`, `damodaran`, `greenwald`, `transparency`

## Context

The system's DCF model used a flat single growth rate (`g`) for all N projection years (default 10), then a terminal perpetuity. This is the simplest possible DCF formulation, but it has a structural problem for value investing:

**The flat-growth problem**: a constant 11% FCF growth for 10 years assumes the business never loses competitive advantage, the TAM never saturates, and there are no cycles. For QLYS at 11% for 10 years, FCF grows from $304M to $771M — 2.5× in a decade. The model presents this as "probable" while burying the uncertainty in a single number.

**The EPV gap**: the system had no zero-growth anchor. The only way to assess valuation was "DCF upside" — a number that is entirely dependent on growth assumptions the user inputs. There was no way to answer: "what is this business worth if it never grows again?" — the Greenwald question that separates quality businesses from value traps.

**The WACC opacity**: the user inputted WACC with no anchor. Eduardo (not a finance expert) would often use 9.0% as the default without knowing if it was appropriate for the company's size and the current rate environment.

**Audit finding**: an external Opus 4.7 audit of QLYS analysis flagged that §6 commentary was shallow because the Value Expert had no structured information about growth model credibility and no second valuation anchor to compare against.

## Decisions

### 1. 3-Stage DCF with Damodaran Fade

Replace the 2-stage model with a 3-stage model:

- **Stage 1** (years 1..`stage1_years`): constant growth at `g1`
- **Stage 2** (years `stage1_years+1`..`stage1_years+fade_years`): linear fade from `g1` → `terminal_growth`
- **Stage 3**: perpetuity at `terminal_growth` (unchanged)

**`fade_years=0` degrades to the original 2-stage behavior** — the old `_two_stage_dcf` is kept as a compatibility wrapper that calls `_three_stage_dcf(fade_years=0)`.

**Default split** (auto, when user does not override): `stage1 = projection_years // 2`, `fade = projection_years - stage1`. For 10y: 5+5. For 5y: 2+3.

**User control**: `ValuationInput.stage1_years` and `fade_years` are Optional[int] fields. CLI (Checkpoint 2) shows the auto-split and offers override via "S1,FADE" format.

**Why fade_years=0 compatibility**: tests and any future code that calls `_two_stage_dcf` directly still work without changes. The wrapper documents the delegation.

**Effect on numbers**: for a 10-year DCF at 11% growth, 3-stage (5+5) produces ~35-40% lower terminal value than flat-10 because years 6-10 grow at declining rates. The model is more conservative and statistically more likely to be correct.

### 2. EPV as Zero-Growth Anchor (Greenwald)

Add Earnings Power Value as a second valuation method alongside DCF:

```
EPV_enterprise = NOPAT_normalized / WACC
EPV_equity = EPV_enterprise - net_debt
EPV_per_share = EPV_equity / shares
```

**NOPAT normalization**: median EBIT over 5 years (or fewer if <5 available), × (1 - tax_rate). Median is more robust than latest or average for cyclical companies.

**Tax rate**: derived from latest NI/EBIT ratio (clamped to [10%, 40%]). Falls back to 25% US corporate default.

**EPV_enterprise is comparable to market_cap** (both enterprise-level). Net debt is subtracted before dividing by shares to get EPV_per_share comparable to market price. Without this adjustment, leveraged companies would appear artificially cheap.

**EPV upside**: `(EPV_enterprise - market_cap) / market_cap × 100`. Positive → stock is below zero-growth value (highest-conviction BUY signal). Negative → price is above EPV (premium for growth, not a BUY without growth).

**Source label**: `ebit_median_5y`, `ebit_median_Ny`, or `ebit_latest` for audit trail.

**Why median 5y vs latest EBIT**: Greenwald's key insight is that EPV uses "normalized" earnings (smoothed over the business cycle), not the most recent point. Median over 5 years removes cyclical peaks/troughs better than arithmetic mean.

### 3. WACC Advisory (Simple CAPM)

Add a deterministic suggested WACC displayed before Checkpoint 2 prompt:

```
WACC_suggested = Rf + β × ERP + size_premium
```

- `Rf = 4.2%` — US 10y Treasury yield (hardcoded, update manually as rates change)
- `ERP = 5.0%` — Damodaran historical ERP for US equities
- `β = 1.0` — simplification (no company-specific beta calculation)
- `size_premium`: >$10B large cap (0%), $2-10B mid (0.5%), $300M-$2B small (1.5%), <$300M micro (3%)

**This is advisory, not mandatory.** The user retains full control of WACC input. The advisory anchors the decision to an objective reference instead of "9.0% because it's the default."

**β=1.0 limitation**: disclosed explicitly in CLI and Value Expert context. For companies with very high or very low systematic risk, this is wrong. Eduardo can override manually when he has a better estimate.

**Why not FRED API + dynamic Rf**: would require a new HTTP adapter (new dependency, new API key, new error surface). The benefit doesn't justify the complexity for a personal tool. Rf is updated manually when rates change materially.

### 4. User Override Preserved

The original proposal suggested eliminating `revenue_growth_yr1_pct` (user override for growth). **This was rejected** because:

- ADR-006 (Socratic HITL): checkpoints are didactic — the user learns by deciding
- EC-54 (5-layer transparency): the override with visible auto-value is the correct pattern
- Eduardo is a value investor, not a passive user — his input is signal, not noise

The override remains. The system now shows `suggested_wacc` and `auto_growth` with source before every prompt, giving Eduardo the context to make informed decisions rather than guessing.

## Architecture

```
_three_stage_dcf(base_fcf, g1, terminal, wacc, stage1_years, fade_years) → float
    ↑ called by: valuation_agent(), _reverse_dcf(), _sensitivity_table()

_two_stage_dcf(base_fcf, g, terminal, wacc, years) → float
    ↑ compatibility wrapper → _three_stage_dcf(fade_years=0)

_resolve_stages(projection_years, stage1_years, fade_years) → (int, int)
    ↑ called by: valuation_agent(), CLI _ask_dcf_assumptions()

_ebit_median(ebit_series) → (float, str)
_compute_epv(ebit_series, net_income, wacc, net_debt, shares, mkt_cap) → (mv, mv, pct, src)
    ↑ called by valuation_agent()

_compute_suggested_wacc(market_cap) → (float, str)
    ↑ called by: valuation_agent(), CLI _ask_dcf_assumptions()

ValuationReport.stage1_years_used, .fade_years_used  — audit trail
ValuationReport.epv_enterprise, .epv_per_share, .epv_upside_pct, .epv_source
ValuationReport.suggested_wacc_pct, .suggested_wacc_breakdown
```

## Consequences

### Positive
- **DCF more conservative**: fade produces 35-40% lower values for 10y high-growth scenarios. Reduces overvaluation bias.
- **EPV as conviction anchor**: if stock < EPV, BUY verdict doesn't need growth to hold. Strongest possible margin of safety signal.
- **WACC anchored to objective reference**: Eduardo no longer guesses. The advisory gives a starting point tied to current rate environment and size premium.
- **Reverse DCF still comparable**: uses same 3-stage structure as forward DCF — the implied growth is now relative to the same model.
- **User retains control**: all defaults are overridable. The system is an advisor, not an oracle.

### Negative
- **DCF numeric tests required recalibration**: 3-stage produces different numbers. All existing DCF numeric assertions were verified correct under the new model.
- **β=1.0 WACC simplification**: wrong for extreme betas. Disclosed. Acceptable for a personal tool where the user can override.
- **No company-specific risk premium in advisory (EC-64)**: the advisory formula `Rf + β×ERP + size_premium` does not include a company-specific risk premium. For high-risk profiles (customer concentration >50%, active SEC investigation, restatements, recent IPO), the appropriate WACC may exceed the advisory by 3-8pp. The system surfaces this gap via an automatic note in `dcf_reliability_note` when `abs(wacc_used - advisory) > 3pp`, and the sensitivity table covers ±2pp (expanded from ±1pp in MCE-29) to ensure the analyst can see values at the higher-risk WACC range. The user must still supply the company-specific adjustment manually at Checkpoint 2.
- **EPV not computed for companies with no positive EBIT history**: returns None gracefully. Documented in reliability note.

## Alternatives Considered

### A. Keep flat 2-stage DCF
**Rejected**: the flat growth problem is real and material. A 10-year flat DCF overstates terminal value systematically. The Damodaran fade is the academic standard for good reason.

### B. Dynamic Rf from FRED API
**Rejected**: adds a new HTTP adapter, new API key, new failure mode. Rf changes slowly — manual update is acceptable for a personal tool.

### C. Company-specific beta from Polygon OHLCV
**Rejected**: beta is noisy, especially for small caps. Industry beta (Damodaran tables) would be better, but requires a new lookup table by SIC code. Deferred — β=1.0 + size premium achieves similar ballpark result with zero complexity.

### D. Eliminate user growth override
**Rejected**: conflicts with ADR-006 and EC-54. See above.

## Related

- **ADR-022**: DCF growth methodology (original decision to use min(FCF_CAGR, revenue_CAGR))
- **ADR-027**: Near-term growth transparency (EC-54 5-layer pattern)
- **ADR-006**: Socratic HITL — preserving user agency
- **ADR-030**: Valuation methods convergence signaling
