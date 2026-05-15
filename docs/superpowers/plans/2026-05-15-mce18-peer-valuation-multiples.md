# MCE-18 — Peer Valuation Multiples & Magic Formula Rank

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich peer comparison with EV/EBIT, FCF yield, and Magic Formula rank (Greenblatt), so the report answers "is this company cheap or expensive vs its sector?"

**Architecture:** `peers_node` fetches one Polygon market_cap call per peer and builds a new `PeerValuation` list stored in `state["peer_valuations"]`. `valuation_node` then reads that list alongside the subject's already-computed `earnings_yield_pct` and `roic_series` to populate the stubbed `magic_formula_rank` field via a new `_compute_magic_formula_rank()` helper. Archiver and value_expert get a new relative valuation section each.

**Tech Stack:** Python 3.12, Pydantic v2, LangGraph TypedDict state, `PolygonAdapter.fetch_market_data` (already used for subject), pytest + pytest-mock (zero real API calls)

---

## File Map

| File | Change |
|---|---|
| `src/due_diligence/schemas/reports.py` | Add `PeerValuation` class after `PeerROIC` |
| `src/due_diligence/graph/state.py` | Add `peer_valuations: NotRequired[list[PeerValuation]]` |
| `src/due_diligence/agents/peers.py` | Add `_build_peer_valuations()` helper + import `PolygonAdapter` + call in `peers_node` |
| `src/due_diligence/agents/valuation.py` | Add `_compute_magic_formula_rank()` helper + wire into `valuation_agent` |
| `src/due_diligence/agents/archiver.py` | Add `_render_peer_relative_valuation()` helper + MF rank row in aggregate metrics |
| `src/due_diligence/agents/value_expert.py` | Add peer multiples context block in `_format_context()` |
| `tests/test_peers_agent.py` | New tests for `_build_peer_valuations` + integration in `peers_node` |
| `tests/test_valuation.py` | New tests for `_compute_magic_formula_rank` + wiring in `valuation_agent` |
| `tests/test_archiver.py` | New tests for relative valuation table + MF rank row |

**Files NOT changed:** `graph.py` (no new nodes), `competitive.py`, `PeerROIC` schema, `CompetitiveReport`.

---

## Task 1: PeerValuation schema + state key

**Files:**
- Modify: `src/due_diligence/schemas/reports.py` (after line 84, after `PeerROIC` class)
- Modify: `src/due_diligence/graph/state.py` (after line 75, in peer comparison section)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_peers_agent.py`:

```python
def test_peer_valuation_schema_fields_and_defaults():
    """PeerValuation schema has correct fields and default values."""
    from due_diligence.schemas.reports import PeerValuation

    pv = PeerValuation(ticker="MSFT", roic=0.341, period="FY2025")
    assert pv.ticker == "MSFT"
    assert pv.roic == 0.341
    assert pv.period == "FY2025"
    assert pv.ev_ebit is None
    assert pv.fcf_yield_pct is None
    assert pv.mf_rank is None
    assert pv.no_market_data is False


def test_peer_valuation_schema_with_all_fields():
    """PeerValuation accepts all optional fields."""
    from due_diligence.schemas.reports import PeerValuation

    pv = PeerValuation(
        ticker="GOOGL",
        roic=0.223,
        period="FY2025",
        ev_ebit=14.1,
        fcf_yield_pct=4.9,
        mf_rank=1,
        no_market_data=False,
    )
    assert pv.ev_ebit == 14.1
    assert pv.mf_rank == 1


def test_peer_valuations_state_key_exists():
    """peer_valuations is a valid key in AnalysisState TypedDict."""
    from due_diligence.schemas.reports import PeerValuation
    from due_diligence.graph.state import AnalysisState
    import typing

    hints = typing.get_type_hints(AnalysisState, include_extras=True)
    assert "peer_valuations" in hints
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_peers_agent.py::test_peer_valuation_schema_fields_and_defaults tests/test_peers_agent.py::test_peer_valuation_schema_with_all_fields tests/test_peers_agent.py::test_peer_valuations_state_key_exists -v
```

Expected: FAIL with `ImportError: cannot import name 'PeerValuation'`

- [ ] **Step 3: Add PeerValuation to reports.py**

In `src/due_diligence/schemas/reports.py`, after line 84 (after the `PeerROIC` class), add:

```python
class PeerValuation(BaseModel):
    """
    Peer ticker with valuation multiples for relative comparison. MCE-18.

    Built in peers_node via PolygonAdapter (market_cap) + XBRL (EBIT, FCF, debt, cash).
    mf_rank is None until valuation_node computes it from the full subject+peer set.
    """
    ticker: str
    roic: float                              # Most recent ROIC ratio (e.g. 0.341 = 34.1%)
    period: str                              # Fiscal year label (e.g. "FY2025")
    ev_ebit: Optional[float] = None          # EV / EBIT (None if no market data or EBIT ≤ 0)
    fcf_yield_pct: Optional[float] = None    # FCF / market_cap × 100 (None if FCF ≤ 0 or no market data)
    mf_rank: Optional[int] = None            # Magic Formula rank (1 = best); set by valuation_node
    no_market_data: bool = False             # True if Polygon returned no market_cap for this peer
```

- [ ] **Step 4: Add peer_valuations to state.py**

In `src/due_diligence/graph/state.py`:

Add to imports at line 35 (extend the `from due_diligence.schemas.reports import (` block):

```python
from due_diligence.schemas.reports import (
    CriticReport,
    CompetitiveReport,
    FilingsRAGReport,
    ManagementReport,
    MarketReport,
    PeerValuation,
    ValuationReport,
    ValueExpertReport,
)
```

Add to `AnalysisState` TypedDict after line 75 (in the `# --- Peer comparison Phase 2` block):

```python
    peer_valuations: NotRequired[list[PeerValuation]]  # EV/EBIT, FCF yield, MF rank per peer (MCE-18)
```

- [ ] **Step 5: Run test to verify it passes**

```
uv run pytest tests/test_peers_agent.py::test_peer_valuation_schema_fields_and_defaults tests/test_peers_agent.py::test_peer_valuation_schema_with_all_fields tests/test_peers_agent.py::test_peer_valuations_state_key_exists -v
```

Expected: PASS (3 tests)

- [ ] **Step 6: Run full suite to verify no regressions**

```
uv run pytest -q
```

Expected: all previously passing tests still pass

- [ ] **Step 7: Commit**

```
git add src/due_diligence/schemas/reports.py src/due_diligence/graph/state.py tests/test_peers_agent.py
git commit -m "feat(mce-18): add PeerValuation schema and state key"
```

---

## Task 2: `_build_peer_valuations` helper + integrate into `peers_node`

**Files:**
- Modify: `src/due_diligence/agents/peers.py`
- Modify: `tests/test_peers_agent.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_peers_agent.py`:

```python
def test_build_peer_valuations_computes_ev_ebit_and_fcf_yield(mocker):
    """Happy path: computes EV/EBIT and FCF yield from XBRL + Polygon data."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue
    from unittest.mock import MagicMock

    fake_rep = MagicMock()
    fake_rep.ticker = "MSFT"
    fake_rep.roic_series = [
        MetricValue(value=0.341, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=110_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=74_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=50_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=18_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 3_000_000_000_000.0, "price_current": 420.0},
    )

    result = _build_peer_valuations([fake_rep])

    assert len(result) == 1
    pv = result[0]
    assert pv.ticker == "MSFT"
    assert pv.roic == pytest.approx(0.341)
    assert pv.no_market_data is False
    # EV = 3_000B + 50B - 18B = 3_032B
    # EV/EBIT = 3_032B / 110B ≈ 27.56
    assert pv.ev_ebit == pytest.approx(27.56, abs=0.1)
    # FCF yield = 74B / 3_000B * 100 ≈ 2.47%
    assert pv.fcf_yield_pct == pytest.approx(2.47, abs=0.1)
    assert pv.mf_rank is None  # set by valuation_node later


def test_build_peer_valuations_handles_no_market_cap(mocker):
    """Peer with no Polygon market_cap gets no_market_data=True, multiples=None."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue
    from unittest.mock import MagicMock

    fake_rep = MagicMock()
    fake_rep.ticker = "FOREIGN_CO"
    fake_rep.roic_series = [
        MetricValue(value=0.15, unit="ratio", period="FY2025", source="calculated")
    ]

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": None, "price_current": None},
    )

    result = _build_peer_valuations([fake_rep])

    assert len(result) == 1
    pv = result[0]
    assert pv.no_market_data is True
    assert pv.ev_ebit is None
    assert pv.fcf_yield_pct is None


def test_build_peer_valuations_handles_negative_fcf(mocker):
    """Peer with FCF < 0 gets fcf_yield_pct=None but ev_ebit still computed."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue
    from unittest.mock import MagicMock

    fake_rep = MagicMock()
    fake_rep.ticker = "REINVEST_CO"
    fake_rep.roic_series = [
        MetricValue(value=0.158, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=60_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=-5_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=80_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=90_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 2_000_000_000_000.0},
    )

    result = _build_peer_valuations([fake_rep])

    pv = result[0]
    assert pv.fcf_yield_pct is None
    assert pv.ev_ebit is not None  # EV/EBIT still computable
    assert pv.no_market_data is False


def test_build_peer_valuations_handles_nonpositive_ebit(mocker):
    """Peer with EBIT ≤ 0 gets ev_ebit=None but fcf_yield still computed if FCF > 0."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue
    from unittest.mock import MagicMock

    fake_rep = MagicMock()
    fake_rep.ticker = "LOSS_CO"
    fake_rep.roic_series = [
        MetricValue(value=0.05, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=-10_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=5_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=20_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=10_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 500_000_000_000.0},
    )

    result = _build_peer_valuations([fake_rep])

    pv = result[0]
    assert pv.ev_ebit is None
    assert pv.fcf_yield_pct is not None  # FCF > 0, market_cap > 0


def test_peers_node_returns_peer_valuations_in_output(mocker):
    """peers_node includes peer_valuations in its return dict when peers are provided."""
    from due_diligence.agents.peers import peers_node
    from due_diligence.schemas.base import MetricValue
    from unittest.mock import MagicMock

    fake_rep = MagicMock()
    fake_rep.ticker = "MSFT"
    fake_rep.roic_series = [
        MetricValue(value=0.341, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=110_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=74_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=50_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=18_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch("due_diligence.agents.peers.EdgarAdapter.fetch_raw", return_value={"ticker": "MSFT"})
    mocker.patch("due_diligence.agents.peers.EdgarAdapter.parse_financials", return_value=fake_rep)
    mocker.patch("due_diligence.agents.peers._get_sic", return_value=("7372", "Software"))
    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 3_000_000_000_000.0},
    )

    result = peers_node({"ticker": "AAPL", "peers": ["MSFT"]})

    assert "peer_valuations" in result
    assert len(result["peer_valuations"]) == 1
    pv = result["peer_valuations"][0]
    assert pv.ticker == "MSFT"
    assert pv.ev_ebit is not None
    assert pv.mf_rank is None  # set later by valuation_node


def test_peers_node_empty_list_does_not_add_peer_valuations():
    """peers_node with empty peer list returns no peer_valuations key."""
    from due_diligence.agents.peers import peers_node

    result = peers_node({"ticker": "AAPL", "peers": []})

    assert "peer_valuations" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_peers_agent.py::test_build_peer_valuations_computes_ev_ebit_and_fcf_yield tests/test_peers_agent.py::test_build_peer_valuations_handles_no_market_cap tests/test_peers_agent.py::test_build_peer_valuations_handles_negative_fcf tests/test_peers_agent.py::test_build_peer_valuations_handles_nonpositive_ebit tests/test_peers_agent.py::test_peers_node_returns_peer_valuations_in_output tests/test_peers_agent.py::test_peers_node_empty_list_does_not_add_peer_valuations -v
```

Expected: FAIL with `ImportError: cannot import name '_build_peer_valuations'`

- [ ] **Step 3: Implement `_build_peer_valuations` in peers.py**

Add `from due_diligence.tools.polygon import PolygonAdapter` and `from due_diligence.schemas.reports import PeerROIC, PeerValuation` to the imports section of `src/due_diligence/agents/peers.py`. Replace the existing import of `PeerROIC`:

```python
from due_diligence.schemas.reports import PeerROIC, PeerValuation
from due_diligence.tools.edgar import DataUnavailableError, EdgarAdapter
from due_diligence.tools.polygon import PolygonAdapter
```

Add this function after `_get_sic()` (before `peers_node()`):

```python
def _build_peer_valuations(reports: list) -> list[PeerValuation]:
    """
    Build PeerValuation list from FinancialsReport objects + Polygon market data. MCE-18.

    For each peer: fetches market_cap via Polygon, computes EV/EBIT and FCF yield.
    Graceful degradation: no Polygon data → no_market_data=True, multiples=None.
    Peers with EBIT ≤ 0 get ev_ebit=None. Peers with FCF ≤ 0 get fcf_yield_pct=None.
    mf_rank is always None here — set by valuation_node after the full set is known.
    """
    result: list[PeerValuation] = []

    for rep in reports:
        if not rep.roic_series:
            log.warning("peer_valuation_skipped_no_roic", peer=rep.ticker)
            continue

        roic = rep.roic_series[0].value
        period = rep.roic_series[0].period

        mkt = PolygonAdapter.fetch_market_data(rep.ticker)
        market_cap: float | None = mkt.get("market_cap")

        if market_cap is None or market_cap <= 0:
            result.append(PeerValuation(
                ticker=rep.ticker,
                roic=roic,
                period=period,
                no_market_data=True,
            ))
            log.warning("peer_valuation_no_market_data", peer=rep.ticker)
            continue

        ebit: float | None = rep.operating_income[0].value if rep.operating_income else None
        fcf: float | None = rep.free_cash_flow[0].value if rep.free_cash_flow else None
        debt: float = rep.total_debt.value if rep.total_debt is not None else 0.0
        cash: float = rep.cash_and_equivalents.value if rep.cash_and_equivalents is not None else 0.0

        ev = market_cap + debt - cash

        ev_ebit: float | None = None
        if ebit is not None and ebit > 0 and ev > 0:
            ev_ebit = round(ev / ebit, 2)

        fcf_yield_pct: float | None = None
        if fcf is not None and fcf > 0:
            fcf_yield_pct = round(fcf / market_cap * 100, 2)

        result.append(PeerValuation(
            ticker=rep.ticker,
            roic=roic,
            period=period,
            ev_ebit=ev_ebit,
            fcf_yield_pct=fcf_yield_pct,
        ))
        log.info("peer_valuation_built", peer=rep.ticker, ev_ebit=ev_ebit, fcf_yield_pct=fcf_yield_pct)

    return result
```

- [ ] **Step 4: Call `_build_peer_valuations` in `peers_node`**

In `peers_node()`, after the existing block that handles `peer_roic` (after the `elif reports and not peer_roic:` block, before `return out`), add:

```python
    # Build peer_valuations (MCE-18): EV/EBIT, FCF yield via Polygon market data.
    # mf_rank is None here — set by valuation_node once the full subject+peer set is known.
    if reports:
        peer_valuations = _build_peer_valuations(reports)
        if peer_valuations:
            out["peer_valuations"] = peer_valuations
            log.info("peer_valuations_built", count=len(peer_valuations))

    return out
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_peers_agent.py::test_build_peer_valuations_computes_ev_ebit_and_fcf_yield tests/test_peers_agent.py::test_build_peer_valuations_handles_no_market_cap tests/test_peers_agent.py::test_build_peer_valuations_handles_negative_fcf tests/test_peers_agent.py::test_build_peer_valuations_handles_nonpositive_ebit tests/test_peers_agent.py::test_peers_node_returns_peer_valuations_in_output tests/test_peers_agent.py::test_peers_node_empty_list_does_not_add_peer_valuations -v
```

Expected: PASS (6 tests)

- [ ] **Step 6: Run full suite**

```
uv run pytest -q
```

Expected: all previously passing tests still pass

- [ ] **Step 7: Commit**

```
git add src/due_diligence/agents/peers.py tests/test_peers_agent.py
git commit -m "feat(mce-18): compute peer EV/EBIT and FCF yield in peers_node"
```

---

## Task 3: `_compute_magic_formula_rank` pure function

**Files:**
- Modify: `src/due_diligence/agents/valuation.py`
- Modify: `tests/test_valuation.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_valuation.py`:

```python
# ---------------------------------------------------------------------------
# Tests: _compute_magic_formula_rank (MCE-18)
# ---------------------------------------------------------------------------

def test_compute_magic_formula_rank_assigns_unique_ranks_to_all_participants():
    """All 4 participants (subject + 3 peers) get unique ranks 1–4."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    peers = [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1),
        PeerValuation(ticker="AMZN", roic=0.158, period="FY2025", ev_ebit=22.7),
    ]
    # Subject: earnings_yield=8.13% (≈1/12.3*100), ROIC=87.4%
    subject_rank, updated_peers = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=8.13,
        subject_roic=0.874,
        peer_valuations=peers,
    )

    assert subject_rank is not None
    peer_ranks = [pv.mf_rank for pv in updated_peers if pv.mf_rank is not None]
    all_ranks = [subject_rank] + peer_ranks
    assert len(all_ranks) == 4
    assert sorted(all_ranks) == [1, 2, 3, 4]


def test_compute_magic_formula_rank_returns_none_with_only_subject():
    """With no valid peers, returns None — minimum 2 participants required."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank

    subject_rank, updated = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=8.13,
        subject_roic=0.874,
        peer_valuations=[],
    )

    assert subject_rank is None
    assert updated == []


def test_compute_magic_formula_rank_subject_excluded_when_earnings_yield_none():
    """Subject with earnings_yield=None (EBIT≤0) is excluded; peers still get ranks."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    peers = [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1),
    ]

    subject_rank, updated = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=None,  # EBIT ≤ 0 — subject excluded
        subject_roic=0.874,
        peer_valuations=peers,
    )

    assert subject_rank is None
    # Peers still get ranked among themselves
    peer_ranks = [pv.mf_rank for pv in updated if pv.mf_rank is not None]
    assert sorted(peer_ranks) == [1, 2]


def test_compute_magic_formula_rank_excludes_peers_with_no_market_data():
    """Peers with no_market_data=True are excluded from ranking; mf_rank stays None."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    peers = [
        PeerValuation(ticker="VALID", roic=0.20, period="FY2025", ev_ebit=15.0),
        PeerValuation(ticker="NODATA", roic=0.30, period="FY2025", no_market_data=True),
    ]

    subject_rank, updated = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=8.13,
        subject_roic=0.874,
        peer_valuations=peers,
    )

    assert subject_rank in (1, 2)
    valid_pv = next(pv for pv in updated if pv.ticker == "VALID")
    nodata_pv = next(pv for pv in updated if pv.ticker == "NODATA")
    assert valid_pv.mf_rank is not None
    assert nodata_pv.mf_rank is None  # excluded, rank unchanged


def test_compute_magic_formula_rank_excludes_peers_with_none_ev_ebit():
    """Peers with ev_ebit=None (EBIT≤0) are excluded from ranking."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    peers = [
        PeerValuation(ticker="VALID", roic=0.20, period="FY2025", ev_ebit=15.0),
        PeerValuation(ticker="LOSS_CO", roic=0.05, period="FY2025", ev_ebit=None),
    ]

    subject_rank, updated = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=8.13,
        subject_roic=0.874,
        peer_valuations=peers,
    )

    loss_pv = next(pv for pv in updated if pv.ticker == "LOSS_CO")
    assert loss_pv.mf_rank is None


def test_compute_magic_formula_rank_high_roic_and_low_ev_ebit_gets_best_rank():
    """The company with best combined earnings yield AND ROIC gets rank #1."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    # Subject: high ROIC (87.4%), low EV/EBIT → high earnings yield → should rank #1
    peers = [
        # PEER_A: mediocre on both
        PeerValuation(ticker="PEER_A", roic=0.10, period="FY2025", ev_ebit=30.0),
    ]

    subject_rank, _ = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=12.0,  # high earnings yield (low EV/EBIT)
        subject_roic=0.874,           # high ROIC
        peer_valuations=peers,
    )

    assert subject_rank == 1  # AAPL dominates on both metrics
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_valuation.py::test_compute_magic_formula_rank_assigns_unique_ranks_to_all_participants tests/test_valuation.py::test_compute_magic_formula_rank_returns_none_with_only_subject tests/test_valuation.py::test_compute_magic_formula_rank_subject_excluded_when_earnings_yield_none tests/test_valuation.py::test_compute_magic_formula_rank_excludes_peers_with_no_market_data tests/test_valuation.py::test_compute_magic_formula_rank_excludes_peers_with_none_ev_ebit tests/test_valuation.py::test_compute_magic_formula_rank_high_roic_and_low_ev_ebit_gets_best_rank -v
```

Expected: FAIL with `ImportError: cannot import name '_compute_magic_formula_rank'`

- [ ] **Step 3: Implement `_compute_magic_formula_rank` in valuation.py**

Add `PeerValuation` to the imports in `src/due_diligence/agents/valuation.py`:

```python
from due_diligence.schemas.reports import ValuationInput, ValuationReport, PeerValuation
```

Add this function before `valuation_agent()` in `src/due_diligence/agents/valuation.py`:

```python
def _compute_magic_formula_rank(
    subject_ticker: str,
    subject_earnings_yield: Optional[float],
    subject_roic: Optional[float],
    peer_valuations: list[PeerValuation],
) -> tuple[Optional[int], list[PeerValuation]]:
    """
    Compute Magic Formula rank (Greenblatt) for subject and all valid peers.

    Algorithm: rank by earnings yield (EBIT/EV, higher=better) + rank by ROIC
    (higher=better). Combined score = sum of both ranks (lower=better). Returns
    (subject_rank, updated_peer_valuations_with_mf_rank_filled).

    Requires ≥ 2 valid participants; returns (None, unchanged_peers) otherwise.
    Excluded: peers with no_market_data=True or ev_ebit=None; subject with
    earnings_yield=None or roic=None.
    """
    participants: list[dict] = []

    # Add subject if it has valid data for both ranking components
    if (
        subject_earnings_yield is not None
        and subject_earnings_yield > 0
        and subject_roic is not None
    ):
        participants.append({
            "ticker": subject_ticker,
            "earnings_yield": subject_earnings_yield,  # percentage, higher = better
            "roic": subject_roic,                       # ratio, higher = better
            "is_subject": True,
        })

    # Add peers that have valid EV/EBIT (ev_ebit > 0, market data available)
    for pv in peer_valuations:
        if pv.no_market_data or pv.ev_ebit is None or pv.ev_ebit <= 0:
            continue
        # Convert EV/EBIT to earnings yield: earnings_yield = 100 / ev_ebit
        participants.append({
            "ticker": pv.ticker,
            "earnings_yield": round(100.0 / pv.ev_ebit, 4),
            "roic": pv.roic,
            "is_subject": False,
        })

    if len(participants) < 2:
        return None, peer_valuations

    # Rank by earnings yield descending (rank 1 = highest earnings yield = cheapest)
    by_ey = sorted(participants, key=lambda x: x["earnings_yield"], reverse=True)
    for i, p in enumerate(by_ey):
        p["ey_rank"] = i + 1

    # Rank by ROIC descending (rank 1 = highest ROIC = best quality)
    by_roic = sorted(participants, key=lambda x: x["roic"], reverse=True)
    for i, p in enumerate(by_roic):
        p["roic_rank"] = i + 1

    # Combined score — lower is better (Greenblatt)
    for p in participants:
        p["combined"] = p["ey_rank"] + p["roic_rank"]

    by_combined = sorted(participants, key=lambda x: x["combined"])
    for i, p in enumerate(by_combined):
        p["mf_rank"] = i + 1

    # Subject rank
    subject_rank: Optional[int] = next(
        (p["mf_rank"] for p in by_combined if p["is_subject"]),
        None,
    )

    # Build map of peer ranks by ticker
    peer_rank_map = {
        p["ticker"]: p["mf_rank"]
        for p in by_combined
        if not p["is_subject"]
    }

    # Return updated peer_valuations with mf_rank filled for valid participants
    updated: list[PeerValuation] = [
        pv.model_copy(update={"mf_rank": peer_rank_map[pv.ticker]})
        if pv.ticker in peer_rank_map
        else pv
        for pv in peer_valuations
    ]

    log.info(
        "magic_formula_rank_computed",
        subject=subject_ticker,
        subject_rank=subject_rank,
        participants=len(participants),
    )

    return subject_rank, updated
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_valuation.py::test_compute_magic_formula_rank_assigns_unique_ranks_to_all_participants tests/test_valuation.py::test_compute_magic_formula_rank_returns_none_with_only_subject tests/test_valuation.py::test_compute_magic_formula_rank_subject_excluded_when_earnings_yield_none tests/test_valuation.py::test_compute_magic_formula_rank_excludes_peers_with_no_market_data tests/test_valuation.py::test_compute_magic_formula_rank_excludes_peers_with_none_ev_ebit tests/test_valuation.py::test_compute_magic_formula_rank_high_roic_and_low_ev_ebit_gets_best_rank -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```
git add src/due_diligence/agents/valuation.py tests/test_valuation.py
git commit -m "feat(mce-18): add _compute_magic_formula_rank helper (Greenblatt)"
```

---

## Task 4: Wire `_compute_magic_formula_rank` into `valuation_agent`

**Files:**
- Modify: `src/due_diligence/agents/valuation.py`
- Modify: `tests/test_valuation.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_valuation.py`:

```python
def test_valuation_agent_populates_magic_formula_rank_when_peers_present():
    """valuation_agent reads peer_valuations and populates magic_formula_rank."""
    from due_diligence.agents.valuation import valuation_agent
    from due_diligence.schemas.reports import PeerValuation

    financials = _build_financials()
    market = _build_market_report()

    peers = [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1),
    ]

    state = {
        "ticker": "AAPL",
        "financials": financials,
        "market": market,
        "peer_valuations": peers,
    }

    result = valuation_agent(state)

    assert result["valuation"].magic_formula_rank is not None
    assert "peer_valuations" in result
    ranked_peers = [pv for pv in result["peer_valuations"] if pv.mf_rank is not None]
    assert len(ranked_peers) == 2


def test_valuation_agent_magic_formula_rank_none_when_no_peers():
    """valuation_agent leaves magic_formula_rank=None when no peer_valuations in state."""
    from due_diligence.agents.valuation import valuation_agent

    financials = _build_financials()
    market = _build_market_report()

    state = {
        "ticker": "AAPL",
        "financials": financials,
        "market": market,
        # no peer_valuations key
    }

    result = valuation_agent(state)

    assert result["valuation"].magic_formula_rank is None
    assert "peer_valuations" not in result


def test_valuation_agent_returns_updated_peer_valuations_with_ranks():
    """The returned peer_valuations list has mf_rank populated for valid peers."""
    from due_diligence.agents.valuation import valuation_agent
    from due_diligence.schemas.reports import PeerValuation

    financials = _build_financials()
    market = _build_market_report()

    peers = [
        PeerValuation(ticker="PEER_A", roic=0.10, period="FY2025", ev_ebit=30.0),
    ]

    result = valuation_agent({
        "ticker": "AAPL",
        "financials": financials,
        "market": market,
        "peer_valuations": peers,
    })

    # AAPL has high ROIC and likely positive earnings_yield → rank #1
    assert result["valuation"].magic_formula_rank == 1
    assert result["peer_valuations"][0].ticker == "PEER_A"
    assert result["peer_valuations"][0].mf_rank == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_valuation.py::test_valuation_agent_populates_magic_formula_rank_when_peers_present tests/test_valuation.py::test_valuation_agent_magic_formula_rank_none_when_no_peers tests/test_valuation.py::test_valuation_agent_returns_updated_peer_valuations_with_ranks -v
```

Expected: FAIL — `magic_formula_rank` is `None` (currently hardcoded on line 474)

- [ ] **Step 3: Wire `_compute_magic_formula_rank` into `valuation_agent`**

In `src/due_diligence/agents/valuation.py`, in `valuation_agent()`, replace the line:

```python
        magic_formula_rank=None,
```

with the following block (insert BEFORE the `# --- 9. Construir el report ---` comment, after section 8):

```python
    # --- 9. Magic Formula rank (MCE-18) ---
    # peer_valuations populated by peers_node; mf_rank=None there, computed here.
    peer_valuations_raw: list[PeerValuation] = list(
        state.get("peer_valuations") or []  # type: ignore[typeddict-item]
    )
    subject_roic_for_mf: Optional[float] = None
    if financials is not None and financials.roic_series:
        subject_roic_for_mf = financials.roic_series[0].value

    mf_rank, updated_peer_valuations = _compute_magic_formula_rank(
        subject_ticker=ticker,
        subject_earnings_yield=earnings_yield,
        subject_roic=subject_roic_for_mf,
        peer_valuations=peer_valuations_raw,
    )
```

Then update the `ValuationReport` construction to use `magic_formula_rank=mf_rank`:

```python
    report = ValuationReport(
        ticker=ticker,
        stub=False,
        dcf_intrinsic_value=dcf_value,
        dcf_intrinsic_per_share=dcf_per_share,
        price_current=price_current_mv,
        dcf_upside_pct=dcf_upside,
        reverse_dcf_implied_growth=reverse_growth,
        ev_ebit_multiple=ev_ebit_mv,
        ev_fcf_multiple=ev_fcf_mv,
        pe_multiple=pe_mv,
        earnings_yield_pct=earnings_yield,
        magic_formula_rank=mf_rank,
        sensitivity_table=sensitivity,
        assumptions=assumptions,
        dcf_reliability_note=dcf_reliability_note,
    )
```

Replace the final `return {"valuation": report}` with:

```python
    result: dict = {"valuation": report}
    if updated_peer_valuations:
        result["peer_valuations"] = updated_peer_valuations
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_valuation.py::test_valuation_agent_populates_magic_formula_rank_when_peers_present tests/test_valuation.py::test_valuation_agent_magic_formula_rank_none_when_no_peers tests/test_valuation.py::test_valuation_agent_returns_updated_peer_valuations_with_ranks -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite**

```
uv run pytest -q
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```
git add src/due_diligence/agents/valuation.py tests/test_valuation.py
git commit -m "feat(mce-18): wire magic_formula_rank into valuation_agent"
```

---

## Task 5: Archiver — relative valuation table + MF rank row

**Files:**
- Modify: `src/due_diligence/agents/archiver.py`
- Modify: `tests/test_archiver.py`

- [ ] **Step 1: Write the failing tests**

The existing `tests/test_archiver.py` imports `_render_markdown` directly (line 20). Use it in the new tests to avoid I/O side effects (no need to mock `settings.reports_dir`). Add to `tests/test_archiver.py`:

```python
def _make_peer_valuations_for_archiver():
    """Two PeerValuation objects with all multiples set for archiver tests."""
    from due_diligence.schemas.reports import PeerValuation
    return [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5, fcf_yield_pct=3.2, mf_rank=2),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1, fcf_yield_pct=4.9, mf_rank=1),
    ]


def test_archiver_renders_peer_relative_valuation_table():
    """New ## Peer Comparison — Relative Valuation section appears when peer_valuations present."""
    from due_diligence.agents.archiver import _render_markdown

    state = {
        "ticker": "AAPL",
        "run_id": "test-run",
        "as_of_date": "2025-09-27",
        "peer_valuations": _make_peer_valuations_for_archiver(),
    }

    markdown = _render_markdown(state)

    assert "## Peer Comparison — Relative Valuation" in markdown
    assert "EV/EBIT" in markdown
    assert "FCF Yield" in markdown
    assert "MF Rank" in markdown
    assert "MSFT" in markdown
    assert "GOOGL" in markdown
    assert "Magic Formula rank = combined rank" in markdown


def test_archiver_relative_valuation_section_absent_when_no_peers():
    """Relative Valuation section absent when peer_valuations not in state."""
    from due_diligence.agents.archiver import _render_markdown

    state = {"ticker": "AAPL", "run_id": "test-run", "as_of_date": "2025-09-27"}

    markdown = _render_markdown(state)

    assert "## Peer Comparison — Relative Valuation" not in markdown


def test_archiver_shows_no_market_data_note_for_excluded_peer():
    """Peer with no_market_data=True shows exclusion note in the table."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import PeerValuation

    state = {
        "ticker": "AAPL",
        "run_id": "test-run",
        "as_of_date": "2025-09-27",
        "peer_valuations": [
            PeerValuation(ticker="VALID", roic=0.20, period="FY2025", ev_ebit=15.0, fcf_yield_pct=4.0, mf_rank=1),
            PeerValuation(ticker="NODATA", roic=0.25, period="FY2025", no_market_data=True),
        ],
    }

    markdown = _render_markdown(state)

    assert "NODATA" in markdown
    assert "no market data" in markdown.lower()


def test_archiver_shows_mf_rank_in_aggregate_metrics():
    """Magic Formula rank row appears in the Valuation aggregate metrics table."""
    from due_diligence.agents.archiver import _render_markdown
    from due_diligence.schemas.reports import ValuationReport
    from due_diligence.schemas.base import MetricValue

    state = {
        "ticker": "AAPL",
        "run_id": "test-run",
        "as_of_date": "2025-09-27",
        "peer_valuations": _make_peer_valuations_for_archiver(),
        "valuation": ValuationReport(
            ticker="AAPL",
            stub=False,
            earnings_yield_pct=8.1,
            magic_formula_rank=3,
            ev_ebit_multiple=MetricValue(value=12.3, unit="x", period="FY2025", source="calculated"),
        ),
    }

    markdown = _render_markdown(state)

    assert "Magic Formula rank" in markdown
    assert "#3" in markdown
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_archiver.py::test_archiver_renders_peer_relative_valuation_table tests/test_archiver.py::test_archiver_relative_valuation_section_absent_when_no_peers tests/test_archiver.py::test_archiver_shows_no_market_data_note_for_excluded_peer tests/test_archiver.py::test_archiver_shows_mf_rank_in_aggregate_metrics -v
```

Expected: FAIL (section not rendered yet)

- [ ] **Step 3: Add `_render_peer_relative_valuation` helper in archiver.py**

Add this function to `src/due_diligence/agents/archiver.py` (after `_render_citations_section`):

```python
def _render_peer_relative_valuation(state: AnalysisState) -> list[str]:
    """
    Render ## Peer Comparison — Relative Valuation table. MCE-18.

    Shows EV/EBIT, FCF yield, ROIC, MF rank for subject + each peer.
    Subject FCF yield computed inline from financials + market (not stored in ValuationReport).
    Returns empty list if no peer_valuations in state.
    """
    peer_valuations = state.get("peer_valuations") or []          # type: ignore[typeddict-item]
    if not peer_valuations:
        return []

    has_any_multiples = any(
        pv.ev_ebit is not None or pv.fcf_yield_pct is not None
        for pv in peer_valuations
    )
    if not has_any_multiples:
        return []

    valuation = state.get("valuation")                            # type: ignore[typeddict-item]
    financials = state.get("financials")                          # type: ignore[typeddict-item]
    market = state.get("market")                                  # type: ignore[typeddict-item]
    ticker_label = state.get("ticker", "Subject")                 # type: ignore[typeddict-item]

    lines: list[str] = ["## Peer Comparison — Relative Valuation", ""]
    lines += ["| Ticker | EV/EBIT | FCF Yield | ROIC | MF Rank |", "|---|---|---|---|---|"]

    # --- Subject row ---
    subj_ev_ebit = (
        f"{valuation.ev_ebit_multiple.value:.1f}x"
        if (valuation and valuation.ev_ebit_multiple)
        else "N/A"
    )

    # Compute subject FCF yield from raw data (FCF / market_cap × 100)
    subj_fcf_yield = "N/A"
    if (
        financials and financials.free_cash_flow
        and market and market.market_cap and market.market_cap.value
    ):
        fcf_val = financials.free_cash_flow[0].value
        mkt_val = market.market_cap.value
        if fcf_val > 0 and mkt_val > 0:
            subj_fcf_yield = f"{fcf_val / mkt_val * 100:.1f}%"

    subj_roic = "N/A"
    if financials and financials.roic_series:
        subj_roic = f"{financials.roic_series[0].value:.1%}"

    subj_mf = (
        f"**#{valuation.magic_formula_rank}**"
        if (valuation and valuation.magic_formula_rank is not None)
        else "N/A"
    )

    lines.append(
        f"| **{ticker_label}** | **{subj_ev_ebit}** | **{subj_fcf_yield}** "
        f"| **{subj_roic}** | {subj_mf} ← subject |"
    )

    # --- Peer rows ---
    excluded_tickers: list[str] = []
    for pv in peer_valuations:
        if pv.no_market_data:
            ev_str = "N/A — no market data"
            fcf_str = "N/A — no market data"
            excluded_tickers.append(pv.ticker)
        else:
            ev_str = f"{pv.ev_ebit:.1f}x" if pv.ev_ebit is not None else "N/A (EBIT≤0)"
            fcf_str = f"{pv.fcf_yield_pct:.1f}%" if pv.fcf_yield_pct is not None else "N/A (FCF<0)"
        roic_str = f"{pv.roic:.1%}"
        rank_str = f"#{pv.mf_rank}" if pv.mf_rank is not None else "—"
        lines.append(f"| {pv.ticker} | {ev_str} | {fcf_str} | {roic_str} | {rank_str} |")

    lines.append("")
    lines.append(
        "*Magic Formula rank = combined rank by earnings yield (EBIT/EV) + ROIC (Greenblatt). #1 = best.*"
    )
    if excluded_tickers:
        lines.append(
            f"*{', '.join(excluded_tickers)}: excluded from MF rank — no market data available.*"
        )
    lines.append("")

    return lines
```

- [ ] **Step 4: Call `_render_peer_relative_valuation` in `_render_markdown`**

In `src/due_diligence/agents/archiver.py`, after the existing peer ROIC section (after the `elif competitive and not competitive.stub:` block that ends around line 319), add:

```python
    # --- Peer Relative Valuation (MCE-18): EV/EBIT, FCF yield, MF rank ---
    lines += _render_peer_relative_valuation(state)
```

- [ ] **Step 5: Add MF rank row to aggregate metrics in Valuation section**

In `src/due_diligence/agents/archiver.py`, in the aggregate metrics block, after the `earnings_yield_pct` row (after line 189), add:

```python
        if valuation.magic_formula_rank is not None:
            pv_list = state.get("peer_valuations") or []       # type: ignore[typeddict-item]
            valid_pvs = [pv for pv in pv_list if pv.mf_rank is not None]
            total_participants = len(valid_pvs) + 1            # +1 for subject
            peer_tickers_str = ", ".join(pv.ticker for pv in valid_pvs)
            rows.append((
                "Magic Formula rank",
                f"**#{valuation.magic_formula_rank} of {total_participants}**"
                f" (peers: {peer_tickers_str}) — Greenblatt earnings yield + ROIC",
            ))
```

- [ ] **Step 6: Run tests to verify they pass**

```
uv run pytest tests/test_archiver.py::test_archiver_renders_peer_relative_valuation_table tests/test_archiver.py::test_archiver_relative_valuation_section_absent_when_no_peers tests/test_archiver.py::test_archiver_shows_no_market_data_note_for_excluded_peer tests/test_archiver.py::test_archiver_shows_mf_rank_in_aggregate_metrics -v
```

Expected: PASS (4 tests)

- [ ] **Step 7: Run full suite**

```
uv run pytest -q
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```
git add src/due_diligence/agents/archiver.py tests/test_archiver.py
git commit -m "feat(mce-18): archiver renders peer relative valuation table and MF rank row"
```

---

## Task 6: value_expert — peer multiples context for §6

**Files:**
- Modify: `src/due_diligence/agents/value_expert.py`
- Modify: `tests/test_value_expert.py`

- [ ] **Step 1: Write the failing tests**

The existing `tests/test_value_expert.py` imports `_format_context` directly (line 21). Use it in new tests — no LLM mock needed since `_format_context` is pure string building. Add to `tests/test_value_expert.py`:

```python
def test_value_expert_context_includes_peer_relative_valuation_block():
    """_format_context includes PEER RELATIVE VALUATION block when peer_valuations present."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import PeerValuation, ValuationReport
    from due_diligence.schemas.base import MetricValue

    state = {
        "ticker": "AAPL",
        "peer_valuations": [
            PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5, fcf_yield_pct=3.2, mf_rank=2),
            PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1, fcf_yield_pct=4.9, mf_rank=1),
        ],
        "valuation": ValuationReport(
            ticker="AAPL",
            stub=False,
            ev_ebit_multiple=MetricValue(value=12.3, unit="x", period="FY2025", source="calculated"),
            earnings_yield_pct=8.1,
            magic_formula_rank=3,
        ),
    }

    context = _format_context(state)

    assert "PEER RELATIVE VALUATION" in context
    assert "§6" in context
    assert "EV/EBIT" in context
    assert "Peer median" in context
    assert "MF rank" in context


def test_value_expert_context_peer_block_absent_when_no_peer_valuations():
    """PEER RELATIVE VALUATION block absent when no peer_valuations in state."""
    from due_diligence.agents.value_expert import _format_context

    context = _format_context({"ticker": "AAPL"})

    assert "PEER RELATIVE VALUATION" not in context


def test_value_expert_context_warns_about_excluded_peers():
    """Context includes WARNING for peers with no_market_data=True."""
    from due_diligence.agents.value_expert import _format_context
    from due_diligence.schemas.reports import PeerValuation

    state = {
        "ticker": "AAPL",
        "peer_valuations": [
            PeerValuation(ticker="VALID", roic=0.20, period="FY2025", ev_ebit=15.0, mf_rank=1),
            PeerValuation(ticker="NODATA", roic=0.25, period="FY2025", no_market_data=True),
        ],
    }

    context = _format_context(state)

    assert "NODATA" in context
    assert "excluded" in context.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_value_expert.py::test_value_expert_context_includes_peer_relative_valuation_block tests/test_value_expert.py::test_value_expert_context_peer_block_absent_when_no_peer_valuations tests/test_value_expert.py::test_value_expert_context_warns_about_excluded_peers -v
```

Expected: FAIL — block not yet added to `_format_context`

- [ ] **Step 3: Add peer multiples context block in `_format_context`**

In `src/due_diligence/agents/value_expert.py`, in `_format_context()`, after the `## VALUATION REPORT` block (after line 357, after `lines.append("")`), add:

```python
    # --- Peer Relative Valuation context for §6 (MCE-18) ---
    # Provides LLM with peer medians and MF rank so §6 Valuation can answer
    # "is this company cheap or expensive relative to its sector?"
    peer_valuations_ctx = state.get("peer_valuations") or []  # type: ignore[typeddict-item]
    if peer_valuations_ctx:
        lines.append("## PEER RELATIVE VALUATION (use in §6 — relative cheapness vs sector)")

        valid_ev_ebits = [pv.ev_ebit for pv in peer_valuations_ctx if pv.ev_ebit is not None]
        valid_fcf_yields = [pv.fcf_yield_pct for pv in peer_valuations_ctx if pv.fcf_yield_pct is not None]

        # Subject EV/EBIT vs peer median
        valuation_ctx = state.get("valuation")  # type: ignore[typeddict-item]
        if valuation_ctx and valuation_ctx.ev_ebit_multiple and valid_ev_ebits:
            sorted_ev = sorted(valid_ev_ebits)
            peer_median_ev = sorted_ev[len(sorted_ev) // 2]
            direction = "discount" if valuation_ctx.ev_ebit_multiple.value < peer_median_ev else "premium"
            lines.append(
                f"Subject EV/EBIT: {valuation_ctx.ev_ebit_multiple.value:.1f}x "
                f"| Peer median: {peer_median_ev:.1f}x "
                f"→ subject trades at {direction} to peers"
            )

        # Subject FCF yield vs peer median (computed from raw state — not in ValuationReport)
        financials_ctx = state.get("financials")   # type: ignore[typeddict-item]
        market_ctx = state.get("market")           # type: ignore[typeddict-item]
        if (
            financials_ctx and financials_ctx.free_cash_flow
            and market_ctx and market_ctx.market_cap and market_ctx.market_cap.value
            and valid_fcf_yields
        ):
            fcf_val = financials_ctx.free_cash_flow[0].value
            mkt_val = market_ctx.market_cap.value
            if fcf_val > 0 and mkt_val > 0:
                subj_fcf_yield = fcf_val / mkt_val * 100
                sorted_fcf = sorted(valid_fcf_yields)
                peer_median_fcf = sorted_fcf[len(sorted_fcf) // 2]
                direction_fcf = "above" if subj_fcf_yield > peer_median_fcf else "below"
                lines.append(
                    f"Subject FCF yield: {subj_fcf_yield:.1f}% "
                    f"| Peer median: {peer_median_fcf:.1f}% "
                    f"→ subject FCF yield is {direction_fcf} peer median"
                )

        # MF rank
        if valuation_ctx and valuation_ctx.magic_formula_rank is not None:
            valid_pvs = [pv for pv in peer_valuations_ctx if pv.mf_rank is not None]
            total = len(valid_pvs) + 1
            lines.append(
                f"Subject MF rank: #{valuation_ctx.magic_formula_rank} of {total} "
                f"(Greenblatt earnings yield + ROIC — #1 = best)"
            )

        # Warnings for excluded peers
        excluded = [pv.ticker for pv in peer_valuations_ctx if pv.no_market_data]
        ebit_excluded = [pv.ticker for pv in peer_valuations_ctx if not pv.no_market_data and pv.ev_ebit is None]
        for t in excluded:
            lines.append(f"[WARNING] {t} excluded from MF rank — no market data available")
        for t in ebit_excluded:
            lines.append(f"[WARNING] {t} excluded from MF rank — no valid EBIT for EV/EBIT computation")

        lines.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_value_expert.py::test_value_expert_context_includes_peer_relative_valuation_block tests/test_value_expert.py::test_value_expert_context_peer_block_absent_when_no_peer_valuations tests/test_value_expert.py::test_value_expert_context_warns_about_excluded_peers -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Run full suite**

```
uv run pytest -q
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```
git add src/due_diligence/agents/value_expert.py tests/test_value_expert.py
git commit -m "feat(mce-18): inject peer relative valuation context into value_expert §6"
```

---

## Task 7: MCE-26 compliance check + final verification

**Files:** Read-only analysis — no code changes unless duplicates found

- [ ] **Step 1: Grep for duplicated EV/EBIT calculation patterns**

```
uv run python -c "
import subprocess, sys
r = subprocess.run(['rg', 'ev.*ebit|ev / ebit|ebit.*ev', 'src/', '-i', '--no-heading', '-n'], capture_output=True, text=True)
print(r.stdout or '(no matches)')
"
```

Expected: matches only in `valuation.py` (subject) and `peers.py` (peers). If a third location appears, fix it before proceeding.

- [ ] **Step 2: Grep for any raw `ev_ebit` construction outside the two approved files**

```
uv run python -c "
import subprocess
r = subprocess.run(['rg', 'ev_ebit', 'src/', '-n'], capture_output=True, text=True)
print(r.stdout)
"
```

Expected files: `schemas/reports.py` (schema), `agents/peers.py` (build), `agents/valuation.py` (subject + rank), `agents/archiver.py` (render), `agents/value_expert.py` (context). No others.

- [ ] **Step 3: Run the complete test suite one final time**

```
uv run pytest -q
```

Expected: all tests pass (count should be ≥ 230 + 22 new = ~252)

- [ ] **Step 4: Update MCE-18 status in mce-evolution.md**

In `docs/mce-evolution.md`, move the MCE-18 deferred item to the `## Resueltos` section:

```markdown
### MCE-18 — Peer comparison enriquecida: múltiplos de valoración + Magic Formula — RESUELTO 2026-05-15
EV/EBIT + FCF yield + ROIC per peer. Magic Formula rank (Greenblatt) computed in valuation_node
from peers_node Polygon data. New PeerValuation schema, peer_valuations state key, archiver table,
and value_expert §6 context block. Commit: (backfill after merge).
```

- [ ] **Step 5: Commit**

```
git add docs/mce-evolution.md
git commit -m "docs: MCE-18 resolved — peer valuation multiples + Magic Formula rank"
```

---

## Verification

End-to-end check (requires real API keys):

```bash
# Run a live analysis with 2-3 peers to see the new sections
uv run due-diligence analyze AAPL
# At Checkpoint 1, enter peers: MSFT,GOOGL
# Verify in the output report:
#   1. "## Peer Comparison — Relative Valuation" table appears
#   2. EV/EBIT, FCF Yield, MF Rank columns populated
#   3. "Magic Formula rank" line in Valuation section
#   4. "PEER RELATIVE VALUATION" block in Value Expert context (visible in --debug mode)
```
