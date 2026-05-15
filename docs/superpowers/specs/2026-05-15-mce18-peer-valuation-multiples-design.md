# MCE-18 — Peer Valuation Multiples & Magic Formula Rank

**Date:** 2026-05-15  
**Status:** Approved  
**Scope:** Enrich peer comparison with EV/EBIT, FCF yield, ROIC, and Magic Formula rank (Greenblatt)  
**Approach chosen:** A — enrich `peers_node` + finalize rank in `valuation_node`

---

## Context

The system currently compares peers on ROIC only. For relative valuation — answering "is this company cheap or expensive vs its sector?" — we need price-based multiples per peer.

The Magic Formula rank (Greenblatt) has been stubbed as `None` in `ValuationReport.magic_formula_rank` since Phase 1, explicitly awaiting peer data. This spec closes that gap.

**What this does NOT change:** the ROIC comparison flow (PeerROIC, CompetitiveReport, value_expert §2 Moat) is untouched. We add a parallel valuation track.

---

## Multiples in scope

| Multiple | Formula | Why it matters |
|---|---|---|
| **EV/EBIT** | EV ÷ EBIT (operating income) | Earnings yield denominator; cleaner than P/E (pre-capital structure) |
| **FCF yield** | FCF ÷ market_cap × 100 | Cash generation quality; Buffett/quality-value signal |
| **ROIC** | Already in peers today | Capital efficiency; Magic Formula component 2 |
| ~~P/E~~ | ~~market_cap ÷ net_income~~ | Excluded: GAAP net income too susceptible to one-time items and D&A distortion |

---

## Architecture

### Data flow (no new graph nodes)

```
peers_node
  ├─ [existing] EDGAR XBRL per peer → FinancialsReport (EBIT, FCF, debt, cash, ROIC)
  ├─ [NEW] PolygonAdapter.fetch_market_data(peer_ticker) per peer → market_cap
  ├─ [NEW] Compute per peer: EV = market_cap + debt - cash
  │                          ev_ebit = EV / EBIT
  │                          fcf_yield = FCF / market_cap * 100
  ├─ [NEW] Build PeerValuation list → state["peer_valuations"]
  └─ [EXISTING] PeerROIC → state["competitive"].peer_roic_comparison  (unchanged)

valuation_node  (reads state["peer_valuations"])
  ├─ [existing] Compute subject multiples: earnings_yield_pct, ev_ebit_multiple, etc.
  └─ [NEW] _compute_magic_formula_rank(subject_metrics, peer_valuations)
           → ValuationReport.magic_formula_rank (already stubbed None)

archiver_node
  ├─ [existing] ## Peer Comparison — ROIC table (unchanged)
  ├─ [NEW] ## Peer Comparison — Relative Valuation table
  │         Columns: Ticker | EV/EBIT | FCF Yield | ROIC | MF Rank
  └─ [NEW] Line in Valuation section: "Magic Formula rank: #X of Y (peers: ...)"

value_expert _format_context
  └─ [NEW] Peer multiples injected into §6 Valuation context block
           + warnings for peers excluded from rank (no Polygon data)
```

### Sequencing rationale

`peers_node → checkpoint2 → valuation_node` — peer multiples are computed before valuation_node runs, so `magic_formula_rank` can be finalized there alongside the subject's own `earnings_yield_pct`.

---

## New schema: `PeerValuation`

**File:** `src/due_diligence/schemas/reports.py` (alongside existing `PeerROIC`)

```python
class PeerValuation(BaseModel):
    """Peer ticker with valuation multiples for relative comparison. MCE-18."""
    ticker: str
    roic: float                         # Most recent ROIC ratio (from PeerROIC, reused)
    period: str                         # Fiscal year of the data
    ev_ebit: Optional[float] = None     # EV / EBIT (None if no market data)
    fcf_yield_pct: Optional[float] = None  # FCF / market_cap * 100 (None if FCF<0 or no mkt data)
    mf_rank: Optional[int] = None       # Magic Formula rank among subject+peers (1=best)
    no_market_data: bool = False        # True if Polygon returned no data for this peer
```

**State key:** `peer_valuations: NotRequired[list[PeerValuation]]` added to `AnalysisState`.

---

## Magic Formula rank computation

**Location:** new helper `_compute_magic_formula_rank()` in `valuation.py`

**Algorithm (Greenblatt):**
1. Build a list of all participants: subject + each peer that has valid `ev_ebit` and `roic`
2. Rank by **earnings yield** (EBIT/EV = 1/ev_ebit) — higher is better → rank 1
3. Rank by **ROIC** — higher is better → rank 1
4. Combined score = earnings_yield_rank + roic_rank (lower is better)
5. Sort by combined score ascending → assign `mf_rank` 1, 2, 3...
6. Return subject's rank as `int` → `ValuationReport.magic_formula_rank`
7. Write per-peer ranks back to each `PeerValuation.mf_rank`

**Minimum participants:** if fewer than 2 participants have valid data (e.g., all peers lack Polygon data), `magic_formula_rank = None` with a note.

---

## Graceful degradation

| Scenario | Behavior |
|---|---|
| Polygon returns no data for a peer | `PeerValuation.no_market_data = True`; peer appears in ROIC table, absent from multiples table; warning written to value_expert context: "PEER_X excluded from MF rank — no market data" |
| Peer FCF < 0 | `fcf_yield_pct = None`; peer still participates in MF rank via ROIC + EV/EBIT only; note in table cell: "N/A (FCF<0)" |
| Peer EBIT ≤ 0 | `ev_ebit = None`; peer excluded from MF rank; note in table |
| No peers provided (user skipped) | `peer_valuations = []`; `magic_formula_rank = None`; no new sections in report |
| Subject EBIT ≤ 0 | Subject excluded from MF rank computation; `magic_formula_rank = None` |

All degradations are explicit and logged — no silent fallbacks (ADR-005 / "tools fallan loud").

---

## Report output

### New table in archiver: `## Peer Comparison — Relative Valuation`

```
## Peer Comparison — Relative Valuation

| Ticker | EV/EBIT | FCF Yield | ROIC | MF Rank |
|---|---|---|---|---|
| **AAPL** | **12.3x** | **6.8%** | **87.4%** | **#2** ← subject |
| MSFT | 18.5x | 3.2% | 34.1% | #3 |
| GOOGL | 14.1x | 4.9% | 22.3% | #1 |
| AMZN | 22.7x | 2.1% | 15.8% | #4 |

*Magic Formula rank = combined rank by earnings yield (EBIT/EV) + ROIC (Greenblatt). Lower = better.*
*AMZN: FCF yield N/A (FCF<0 in period).*
```

### New line in Valuation section

```
**Magic Formula rank: #2 of 4** (peers: MSFT, GOOGL, AMZN) — ranked by earnings yield + ROIC
```

### Value Expert context (§6 Valuation)

```
## PEER RELATIVE VALUATION (use in §6)
Subject EV/EBIT: 12.3x | Peer median: 18.5x → subject trades at discount to peers
Subject FCF yield: 6.8% | Peer median: 3.5% → subject generates more cash per dollar of mkt cap
Subject MF rank: #2 of 4 (Greenblatt earnings yield + ROIC)
[WARNING] AMZN excluded from MF rank — no valid EBIT for EV/EBIT computation
```

---

## Files to change

| File | Change |
|---|---|
| `src/due_diligence/schemas/reports.py` | Add `PeerValuation` class (new, ~15 lines) |
| `src/due_diligence/graph/state.py` | Add `peer_valuations: NotRequired[list[PeerValuation]]` |
| `src/due_diligence/agents/peers.py` | Add Polygon call per peer + `PeerValuation` construction |
| `src/due_diligence/agents/valuation.py` | Add `_compute_magic_formula_rank()` helper; populate `magic_formula_rank` |
| `src/due_diligence/agents/archiver.py` | Add `## Peer Comparison — Relative Valuation` table + MF rank line in Valuation section |
| `src/due_diligence/agents/value_expert.py` | Inject peer multiples + warnings into `_format_context()` §6 block |
| `tests/test_peers_agent.py` | New tests for Polygon call + PeerValuation construction + graceful degradation |
| `tests/test_valuation.py` | New tests for `_compute_magic_formula_rank()` with various participant combinations |
| `tests/test_archiver.py` | New tests for relative valuation table rendering |

**Files NOT changed:** `graph.py` (no new nodes), `competitive.py`, `PeerROIC` schema, `CompetitiveReport`.

---

## Invariants respected

- **Zero hallucination numérica (ADR-005):** all multiples computed from XBRL + Polygon — no LLM-generated numbers
- **Provider-agnostic (ADR-004):** Polygon accessed via existing `PolygonAdapter`, not directly
- **Primary-source-first (ADR-011):** financial data from EDGAR XBRL; price/market_cap from Polygon (already approved source)
- **Tools fallan loud:** no silent fallback if Polygon fails — explicit `no_market_data=True` flag + warning
- **Pydantic v2 schemas desde día 0:** `PeerValuation` is a proper Pydantic model, not a free dict
- **Tool-lane purity:** all new computation in `peers_node` and `valuation_node` is deterministic — no LLM involved

---

## Testing strategy

- All tests mock `PolygonAdapter.fetch_market_data` — zero real API calls
- Test `_compute_magic_formula_rank()` with: 2 participants, 4 participants, subject excluded (EBIT≤0), all peers excluded, tie-breaking
- Test graceful degradation: peer with no Polygon data, peer with FCF<0, peer with EBIT≤0
- Archiver test: assert new table present when `peer_valuations` populated; assert table absent when empty
- MCE-26 compliance: after implementing, grep `src/` for any existing EV/EBIT calculation to ensure no duplication

---

## Out of scope

- P/E multiple (excluded by design — net income too distorted)
- EV/FCF as a separate column (FCF yield covers the same signal directionally)  
- Auto-discovery of peers by SIC (ADR-015: user-supplied only)
- Revenue CAGR or growth multiples per peer (MCE-24 territory)
