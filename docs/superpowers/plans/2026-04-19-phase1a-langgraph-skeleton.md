# Phase 1a — LangGraph Skeleton + Financials Specialist

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. MODO PROFESOR: explain each task before implementing, pause after each task.

**Goal:** Stand up the LangGraph graph with `AnalysisState`, Supervisor node, and Financials specialist so that `due-diligence analyze AAPL` runs end-to-end and returns real EDGAR data in a validated state.

**Architecture:** Linear graph for Phase 1a (START → supervisor → financials → END). Supervisor resolves ticker to CIK. Financials specialist wraps the existing `EdgarAdapter`. SQLite checkpointer for persistence. Fan-out with `Send()` is introduced in Phase 1b when the other 4 specialists are ready.

**Tech Stack:** LangGraph (`StateGraph`, `SqliteSaver`, `MemorySaver`), Pydantic v2 (stub schemas for future agents), Typer + Rich (CLI `analyze` command), pytest-mock.

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `src/due_diligence/graph/__init__.py` | Create | Package marker |
| `src/due_diligence/graph/CLAUDE.md` | Create | Conventions for graph layer |
| `src/due_diligence/graph/state.py` | Create | `AnalysisState` TypedDict |
| `src/due_diligence/graph/graph.py` | Create | `build_graph()` — wires nodes and edges |
| `src/due_diligence/agents/__init__.py` | Create | Package marker |
| `src/due_diligence/agents/CLAUDE.md` | Create | MCE-03: specialist agent conventions |
| `src/due_diligence/agents/supervisor.py` | Create | Supervisor node: validate ticker, resolve CIK |
| `src/due_diligence/agents/financials.py` | Create | Financials specialist node |
| `src/due_diligence/schemas/reports.py` | Create | Stub Pydantic models for all agent outputs |
| `src/due_diligence/schemas/__init__.py` | Modify | Re-export new report schemas |
| `src/due_diligence/cli.py` | Modify | Add `analyze` command |
| `tests/test_state.py` | Create | AnalysisState shape validation |
| `tests/test_supervisor.py` | Create | Supervisor node with mocked EDGAR |
| `tests/test_financials_agent.py` | Create | Financials node with fixture |
| `tests/test_graph_1a.py` | Create | End-to-end graph run with MemorySaver + mocks |

---

## Task 1: Stub report schemas

**Files:**
- Create: `src/due_diligence/schemas/reports.py`
- Modify: `src/due_diligence/schemas/__init__.py`

**Why:** `AnalysisState` references output types for all specialists. They don't exist yet — we define them as Pydantic stubs now so the TypedDict compiles and tests can instantiate the State. Each stub has a `stub: bool = True` field so it's always distinguishable from a real output.

- [ ] **Step 1: Write failing test**

Create `tests/test_reports_stubs.py`:

```python
def test_stub_schemas_instantiable():
    from due_diligence.schemas.reports import (
        ManagementReport, FilingsRAGReport, MarketReport,
        CompetitiveReport, ValuationReport, ValueExpertReport, CriticReport,
    )
    assert ManagementReport(ticker="AAPL").stub is True
    assert FilingsRAGReport(ticker="AAPL").stub is True
    assert MarketReport(ticker="AAPL").stub is True
    assert CompetitiveReport(ticker="AAPL").stub is True
    assert ValuationReport(ticker="AAPL").stub is True
    assert ValueExpertReport(ticker="AAPL").stub is True
    assert CriticReport(ticker="AAPL").stub is True
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_reports_stubs.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `src/due_diligence/schemas/reports.py`**

```python
from typing import Optional
from pydantic import BaseModel, Field
from due_diligence.schemas.base import Citation, MetricValue


class ManagementReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1b fields (filled when implemented):
    insider_ownership_pct: Optional[float] = None
    ceo_tenure_years: Optional[float] = None
    form4_transactions_12m: Optional[int] = None
    compensation_structure_notes: Optional[str] = None
    capital_allocation_summary: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


class FilingsRAGReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1b fields:
    business_description: Optional[str] = None
    risk_factors_summary: Optional[str] = None
    mda_highlights: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


class MarketReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1b fields:
    price_current: Optional[MetricValue] = None
    price_52w_high: Optional[MetricValue] = None
    price_52w_low: Optional[MetricValue] = None
    market_cap: Optional[MetricValue] = None
    recent_news_summary: Optional[str] = None


class CompetitiveReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1b fields:
    moat_classification: Optional[str] = None  # "wide", "narrow", "none"
    moat_sources: list[str] = Field(default_factory=list)
    peer_roic_comparison: list[MetricValue] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class CheckpointInput(BaseModel):
    """User input at a Socratic HITL checkpoint. Serializable for CLI and future webapp."""
    checkpoint_id: str          # "checkpoint_1" | "checkpoint_2"
    user_notes: str = ""        # free-form observations
    proceed: bool = True        # False = abort analysis


class ValuationInput(CheckpointInput):
    """Extended input for Checkpoint 2 — valuation assumptions."""
    wacc_pct: float = 9.0              # Weighted Average Cost of Capital
    terminal_growth_pct: float = 2.5   # Terminal growth rate
    projection_years: int = 10
    revenue_growth_yr1_pct: Optional[float] = None
    margin_assumption_pct: Optional[float] = None


class ValuationReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1c fields:
    dcf_intrinsic_value: Optional[MetricValue] = None
    reverse_dcf_implied_growth: Optional[float] = None
    ev_ebitda_multiple: Optional[MetricValue] = None
    pe_multiple: Optional[MetricValue] = None
    magic_formula_rank: Optional[int] = None
    sensitivity_table: list[dict] = Field(default_factory=list)
    assumptions: Optional[ValuationInput] = None


class ValueExpertReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1c fields:
    business_understanding: Optional[str] = None
    moat_analysis: Optional[str] = None
    management_assessment: Optional[str] = None
    financial_quality: Optional[str] = None
    growth_reinvestment: Optional[str] = None
    valuation_section: Optional[str] = None
    risks: Optional[str] = None
    psychological_lens: Optional[str] = None
    decision_frame: Optional[str] = None
    verdict: Optional[str] = None          # MUST always be present (ADR-009)
    verdict_confidence: Optional[float] = None
    suggested_sizing_pct: Optional[float] = None


class CriticReport(BaseModel):
    ticker: str
    stub: bool = True
    # Phase 1c fields:
    weaknesses: list[str] = Field(default_factory=list)
    contra_thesis: Optional[str] = None
    pre_mortem_scenario: Optional[str] = None
```

- [ ] **Step 4: Update `src/due_diligence/schemas/__init__.py`**

```python
from due_diligence.schemas.base import (
    Citation,
    Claim,
    MetricValue,
    FinancialsReport,
)
from due_diligence.schemas.reports import (
    ManagementReport,
    FilingsRAGReport,
    MarketReport,
    CompetitiveReport,
    CheckpointInput,
    ValuationInput,
    ValuationReport,
    ValueExpertReport,
    CriticReport,
)

__all__ = [
    "Citation", "Claim", "MetricValue", "FinancialsReport",
    "ManagementReport", "FilingsRAGReport", "MarketReport", "CompetitiveReport",
    "CheckpointInput", "ValuationInput",
    "ValuationReport", "ValueExpertReport", "CriticReport",
]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_reports_stubs.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/due_diligence/schemas/ tests/test_reports_stubs.py
git commit -m "feat: stub Pydantic schemas for all agent outputs (Phase 1b/1c stubs)"
```

---

## Task 2: MCE gate + directory structure

**Files:**
- Create: `src/due_diligence/graph/__init__.py`
- Create: `src/due_diligence/graph/CLAUDE.md`
- Create: `src/due_diligence/agents/__init__.py`
- Create: `src/due_diligence/agents/CLAUDE.md` ← MCE-03

No test needed — documentation files.

- [ ] **Step 1: Create directories and package markers**

```bash
mkdir -p src/due_diligence/graph src/due_diligence/agents
touch src/due_diligence/graph/__init__.py src/due_diligence/agents/__init__.py
```

- [ ] **Step 2: Write `src/due_diligence/graph/CLAUDE.md`**

```markdown
# CLAUDE.md — graph/

LangGraph graph definition. Wires nodes, edges, and checkpointer.

## Rules
- `state.py` owns `AnalysisState` and nothing else. No business logic.
- `graph.py` owns `build_graph()`. It imports nodes from `agents/` — never implements logic here.
- Never import LangGraph internals outside this directory (agents/ can import state.py).
- `build_graph(checkpointer=None)` — always accept optional checkpointer so tests can pass MemorySaver.
- Thread ID for checkpointer = `run_id` from `AnalysisState`. Always set before invoking.

## Fan-out pattern (Phase 1b)
When adding parallel specialists, use `Send()` from a conditional edge on the supervisor node.
Each Send() passes a copy of the state to one specialist. LangGraph joins automatically.
```

- [ ] **Step 3: Write `src/due_diligence/agents/CLAUDE.md` (MCE-03)**

```markdown
# CLAUDE.md — agents/

Specialist agent nodes for the LangGraph graph.

## Rules
- Each agent is a pure function: `def agent_node(state: AnalysisState) -> dict`.
- Return only the keys you modify — never return the full state.
- Tool-lane agents (Financials, Market): call adapters from `tools/`, no LLM.
- LLM-lane agents (Mgmt, Filings, Competitive): use `get_client()` from `llm/`, emit Citations.
- Every Citation must pass substring-match validation before being included in the return dict.
- Tests MUST mock all external calls (EDGAR, Polygon, Finnhub, LLM). Zero real API calls in pytest.
- When a stub is replaced by a real implementation, flip `stub=False` in its Pydantic output.

## Node signature pattern
```python
def specialist_node(state: AnalysisState) -> dict:
    # reads from state
    ticker = state["ticker"]
    # does work
    report = ...
    # returns only modified keys
    return {"specialist_field": report}
```

## Adding a new specialist (Phase 1b+)
1. Create `agents/<name>.py` with `<name>_node(state) -> dict`.
2. Add corresponding Pydantic output schema in `schemas/reports.py` (remove stub=True).
3. Register node + Send() edge in `graph/graph.py`.
4. Add fixture in `tests/fixtures/`.
5. Write test in `tests/test_<name>_agent.py`.
```

- [ ] **Step 4: Mark MCE-03 in mce-evolution.md**

Open `docs/mce-evolution.md` and move MCE-03 to the Resueltos section:

```markdown
### MCE-03 — Subdirectorio `src/due_diligence/agents/CLAUDE.md` — RESUELTO 2026-04-19
Creado en Phase 1a. Contiene convenciones para specialist agent nodes (función pura, return parcial, tool vs LLM lane, citas obligatorias, testing). Vive en `src/due_diligence/agents/CLAUDE.md`.
```

- [ ] **Step 5: Commit**

```bash
git add src/due_diligence/graph/ src/due_diligence/agents/ docs/mce-evolution.md
git commit -m "docs: MCE-03 agents/CLAUDE.md + graph/ directory with CLAUDE.md"
```

---

## Task 3: AnalysisState

**Files:**
- Create: `src/due_diligence/graph/state.py`
- Create: `tests/test_state.py`

**Why:** `AnalysisState` is the shared memory of the entire graph. Every node reads from it and writes partial updates back. `NotRequired` marks fields that don't exist at invocation time — they appear in the state only after the node that produces them runs.

- [ ] **Step 1: Write failing test**

Create `tests/test_state.py`:

```python
def test_analysis_state_minimal_invocation():
    """State is valid with only ticker — all other fields are NotRequired."""
    from due_diligence.graph.state import AnalysisState
    # TypedDict with NotRequired fields: can construct with only required keys
    state: AnalysisState = {"ticker": "AAPL"}
    assert state["ticker"] == "AAPL"
    assert "financials" not in state


def test_analysis_state_with_all_fields():
    from due_diligence.graph.state import AnalysisState
    from due_diligence.schemas.base import FinancialsReport, MetricValue
    from datetime import date

    def _mv(v: float, period: str) -> MetricValue:
        return MetricValue(value=v, unit="USD", period=period, source="XBRL")

    financials = FinancialsReport(
        ticker="AAPL", cik="0000320193", as_of_date=date(2024, 9, 28),
        revenue=[_mv(391e9, "FY2024")], gross_profit=[_mv(180e9, "FY2024")],
        operating_income=[_mv(123e9, "FY2024")], net_income=[_mv(93e9, "FY2024")],
        operating_cash_flow=[_mv(118e9, "FY2024")], capex=[_mv(9e9, "FY2024")],
        free_cash_flow=[_mv(109e9, "FY2024")],
        total_assets=_mv(364e9, "FY2024"), total_debt=_mv(96e9, "FY2024"),
        cash_and_equivalents=_mv(65e9, "FY2024"),
        shares_outstanding=MetricValue(value=15e9, unit="shares", period="FY2024", source="XBRL"),
        source_filing="10-K FY2024",
    )

    state: AnalysisState = {
        "ticker": "AAPL",
        "cik": "0000320193",
        "as_of_date": "2024-09-28",
        "run_id": "test-run-001",
        "financials": financials,
        "citations": [],
        "unverified_claims": [],
        "cost_usd": 0.0,
    }
    assert state["cik"] == "0000320193"
    assert state["financials"].ticker == "AAPL"
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_state.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `src/due_diligence/graph/state.py`**

```python
from __future__ import annotations

from typing import NotRequired, TypedDict

from due_diligence.schemas.base import Citation, Claim, FinancialsReport
from due_diligence.schemas.reports import (
    ManagementReport,
    FilingsRAGReport,
    MarketReport,
    CompetitiveReport,
    ValuationReport,
    ValueExpertReport,
    CriticReport,
)


class AnalysisState(TypedDict):
    # --- Required at graph invocation ---
    ticker: str

    # --- Set by Supervisor ---
    cik: NotRequired[str]
    as_of_date: NotRequired[str]   # ISO date string — serializable for SQLite checkpointer
    run_id: NotRequired[str]

    # --- Specialist outputs (Phase 1a: financials only; rest are stubs) ---
    financials: NotRequired[FinancialsReport]
    mgmt: NotRequired[ManagementReport]
    filings: NotRequired[FilingsRAGReport]
    market: NotRequired[MarketReport]
    competitive: NotRequired[CompetitiveReport]

    # --- HITL checkpoint inputs (Phase 1b) ---
    checkpoint1_user_input: NotRequired[dict]
    checkpoint2_user_input: NotRequired[dict]

    # --- Downstream agents (Phase 1c) ---
    valuation: NotRequired[ValuationReport]
    synthesis: NotRequired[ValueExpertReport]
    critique: NotRequired[CriticReport]

    # --- Cross-cutting metadata ---
    citations: NotRequired[list[Citation]]
    unverified_claims: NotRequired[list[Claim]]
    cost_usd: NotRequired[float]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_state.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/due_diligence/graph/state.py tests/test_state.py
git commit -m "feat: AnalysisState TypedDict with NotRequired fields for all graph outputs"
```

---

## Task 4: LangGraph graph skeleton

**Files:**
- Create: `src/due_diligence/graph/graph.py`
- Create: `tests/test_graph_1a.py` (partial — completed in Task 7)

**Why:** `build_graph()` is the factory that assembles the graph. It keeps the graph definition separate from the nodes. Accepting `checkpointer=None` means tests pass `MemorySaver()` (in-memory, no file) while production passes `SqliteSaver`.

- [ ] **Step 1: Write failing test**

Create `tests/test_graph_1a.py` (partial, will be extended in Task 7):

```python
def test_build_graph_returns_compiled_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    graph = build_graph(checkpointer=MemorySaver())
    # CompiledGraph has an invoke method
    assert hasattr(graph, "invoke")


def test_build_graph_without_checkpointer():
    from due_diligence.graph.graph import build_graph

    graph = build_graph()
    assert hasattr(graph, "invoke")
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_graph_1a.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `src/due_diligence/graph/graph.py`**

```python
from __future__ import annotations

from typing import Optional

from langgraph.graph import END, START, StateGraph

from due_diligence.agents.financials import financials_node
from due_diligence.agents.supervisor import supervisor_node
from due_diligence.graph.state import AnalysisState


def build_graph(checkpointer=None):
    """
    Phase 1a graph: START → supervisor → financials → END.
    Fan-out with Send() added in Phase 1b when all 5 specialists are ready.
    """
    builder = StateGraph(AnalysisState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("financials", financials_node)

    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", "financials")
    builder.add_edge("financials", END)

    return builder.compile(checkpointer=checkpointer)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_graph_1a.py -v
```

Expected: FAIL (supervisor and financials modules don't exist yet). This is expected — we'll fix it in Tasks 5 and 6.

- [ ] **Step 5: Commit graph skeleton (even with failing imports)**

```bash
git add src/due_diligence/graph/graph.py tests/test_graph_1a.py
git commit -m "feat: LangGraph StateGraph skeleton — supervisor → financials pipeline"
```

---

## Task 5: Supervisor node

**Files:**
- Create: `src/due_diligence/agents/supervisor.py`
- Create: `tests/test_supervisor.py`

**Why:** The Supervisor is the entry point of every analysis. Its job in Phase 1a: validate the ticker exists in EDGAR, resolve it to CIK (permanent identifier), set `as_of_date` from the latest 10-K, generate a `run_id`. It returns a partial state update — only the keys it sets.

- [ ] **Step 1: Write failing tests**

Create `tests/test_supervisor.py`:

```python
import pytest


def test_supervisor_resolves_cik(mocker):
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "AAPL"})

    assert result["cik"] == "0000320193"
    assert result["as_of_date"] == "2024-09-28"
    assert "run_id" in result
    assert len(result["run_id"]) > 0


def test_supervisor_uppercases_ticker(mocker):
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "aapl"})
    assert result["cik"] == "0000320193"


def test_supervisor_invalid_ticker_raises(mocker):
    mocker.patch(
        "due_diligence.agents.supervisor.Company",
        side_effect=Exception("company not found"),
    )

    from due_diligence.agents.supervisor import supervisor_node

    with pytest.raises(ValueError, match="INVALID_TICKER"):
        supervisor_node({"ticker": "INVALID_TICKER"})
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_supervisor.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `src/due_diligence/agents/supervisor.py`**

```python
from __future__ import annotations

import uuid
import structlog

from due_diligence.graph.state import AnalysisState

log = structlog.get_logger()


def supervisor_node(state: AnalysisState) -> dict:
    """
    Entry node. Validates ticker, resolves to CIK, sets run_id and as_of_date.
    Returns partial state update — only the keys this node owns.
    """
    from edgar import Company, set_identity  # type: ignore[import]
    from due_diligence.config import settings

    ticker = state["ticker"].upper()
    set_identity(settings.sec_user_agent)

    try:
        company = Company(ticker)
        cik = str(company.cik).zfill(10)
        latest_10k = company.get_filings(form="10-K").latest()
        as_of_date = str(latest_10k.period_of_report)
    except Exception as e:
        raise ValueError(f"{ticker}: could not resolve ticker in EDGAR — {e}")

    run_id = str(uuid.uuid4())[:12]
    log.info("supervisor_complete", ticker=ticker, cik=cik, as_of_date=as_of_date, run_id=run_id)

    return {
        "ticker": ticker,
        "cik": cik,
        "as_of_date": as_of_date,
        "run_id": run_id,
        "citations": [],
        "unverified_claims": [],
        "cost_usd": 0.0,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_supervisor.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/due_diligence/agents/supervisor.py tests/test_supervisor.py
git commit -m "feat: Supervisor node — ticker validation, CIK resolution, run_id generation"
```

---

## Task 6: Financials specialist node

**Files:**
- Create: `src/due_diligence/agents/financials.py`
- Create: `tests/test_financials_agent.py`

**Why:** The Financials specialist wraps `EdgarAdapter` from Phase 0. It receives the State (with `ticker` and `cik` set by Supervisor), calls `fetch_raw` + `parse_financials`, and returns `{"financials": FinancialsReport(...)}`. Pure tool-lane — no LLM.

- [ ] **Step 1: Write failing tests**

Create `tests/test_financials_agent.py`:

```python
import json
from pathlib import Path


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())


def test_financials_node_returns_report(mocker):
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node
    from due_diligence.schemas.base import FinancialsReport

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    assert "financials" in result
    assert isinstance(result["financials"], FinancialsReport)
    assert result["financials"].ticker == "AAPL"


def test_financials_node_preserves_source_xbrl(mocker):
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    assert result["financials"].revenue[0].source == "XBRL"
    assert result["financials"].free_cash_flow[0].source == "calculated"


def test_financials_node_only_returns_financials_key(mocker):
    """Node must return only the keys it owns — not the full state."""
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    assert set(result.keys()) == {"financials"}
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_financials_agent.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `src/due_diligence/agents/financials.py`**

```python
from __future__ import annotations

import structlog

from due_diligence.graph.state import AnalysisState
from due_diligence.tools.edgar import EdgarAdapter

log = structlog.get_logger()


def financials_node(state: AnalysisState) -> dict:
    """
    Tool-lane specialist. Fetches XBRL financials from EDGAR and returns FinancialsReport.
    No LLM involved — all numbers from primary source.
    """
    ticker = state["ticker"]
    log.info("financials_node_start", ticker=ticker)

    raw = EdgarAdapter.fetch_raw(ticker)
    report = EdgarAdapter.parse_financials(raw)

    log.info("financials_node_complete", ticker=ticker, revenue_periods=len(report.revenue))
    return {"financials": report}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_financials_agent.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Run full suite to verify no regressions**

```bash
uv run pytest -q
```

Expected: all tests pass (count increases by 3).

- [ ] **Step 6: Commit**

```bash
git add src/due_diligence/agents/financials.py tests/test_financials_agent.py
git commit -m "feat: Financials specialist node — wraps EdgarAdapter, pure tool-lane"
```

---

## Task 7: End-to-end graph test + CLI `analyze` command

**Files:**
- Modify: `tests/test_graph_1a.py`
- Modify: `src/due_diligence/cli.py`

- [ ] **Step 1: Extend `tests/test_graph_1a.py` with end-to-end test**

Replace the file content with:

```python
import json
from pathlib import Path

import pytest


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())


def test_build_graph_returns_compiled_graph():
    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    graph = build_graph(checkpointer=MemorySaver())
    assert hasattr(graph, "invoke")


def test_build_graph_without_checkpointer():
    from due_diligence.graph.graph import build_graph

    graph = build_graph()
    assert hasattr(graph, "invoke")


def test_graph_1a_end_to_end(mocker):
    """Full graph run: supervisor + financials with all external calls mocked."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph
    from due_diligence.schemas.base import FinancialsReport

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-run-1a"}}
    result = graph.invoke({"ticker": "AAPL"}, config=config)

    assert result["ticker"] == "AAPL"
    assert result["cik"] == "0000320193"
    assert result["as_of_date"] == "2024-09-28"
    assert "run_id" in result
    assert isinstance(result["financials"], FinancialsReport)
    assert result["financials"].revenue[0].value == pytest.approx(391_035_000_000)


def test_graph_1a_state_has_no_stub_fields_for_later_agents(mocker):
    """Mgmt, filings, market, competitive are absent (NotRequired) — not stubs yet."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from langgraph.checkpoint.memory import MemorySaver
    from due_diligence.graph.graph import build_graph

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-run-1a-b"}}
    result = graph.invoke({"ticker": "AAPL"}, config=config)

    assert "mgmt" not in result
    assert "filings" not in result
    assert "market" not in result
    assert "competitive" not in result
```

- [ ] **Step 2: Run graph tests**

```bash
uv run pytest tests/test_graph_1a.py -v
```

Expected: all 4 PASS.

- [ ] **Step 3: Add `analyze` command to `src/due_diligence/cli.py`**

Add after the existing `get_financials_cmd` function:

```python
@app.command("analyze")
def analyze_cmd(
    ticker: str = typer.Argument(..., help="US stock ticker (e.g. AAPL)"),
) -> None:
    """Run full due diligence analysis pipeline for a US ticker."""
    import sqlite3
    from pathlib import Path
    from langgraph.checkpoint.sqlite import SqliteSaver
    from due_diligence.graph.graph import build_graph

    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    graph = build_graph(checkpointer=checkpointer)

    console.print(f"\n[bold cyan]Due Diligence Analysis — {ticker.upper()}[/bold cyan]")
    console.print("[dim]Phase 1a: Supervisor + Financials specialist[/dim]\n")

    with console.status("[bold green]Running analysis…"):
        config = {"configurable": {"thread_id": f"{ticker.upper()}-latest"}}
        result = graph.invoke({"ticker": ticker.upper()}, config=config)

    console.print(f"[green]✓[/green] Run ID: {result.get('run_id', 'n/a')}")
    console.print(f"[green]✓[/green] CIK resolved: {result.get('cik', 'n/a')}")
    console.print(f"[green]✓[/green] As of date: {result.get('as_of_date', 'n/a')}")

    if result.get("financials"):
        report = result["financials"]
        table = Table(title=f"Financials — {report.source_filing}", show_header=True)
        table.add_column("Metric", style="bold")
        table.add_column("Value (USD B)", justify="right")
        table.add_column("Period")
        table.add_column("Source")

        def _b(v: float) -> str:
            return f"{v / 1e9:.1f}"

        rows = [
            ("Revenue", report.revenue),
            ("Gross Profit", report.gross_profit),
            ("Operating Income", report.operating_income),
            ("Net Income", report.net_income),
            ("Free Cash Flow", report.free_cash_flow),
        ]
        for label, metrics in rows:
            if metrics:
                m = metrics[0]
                table.add_row(label, _b(m.value), m.period, m.source)
        table.add_row("Total Debt", _b(report.total_debt.value), report.total_debt.period, "XBRL")
        table.add_row("Cash + STI", _b(report.cash_and_equivalents.value), report.cash_and_equivalents.period, "XBRL")
        console.print(table)

    console.print("\n[dim]Specialists pending (Phase 1b): mgmt, filings, market, competitive[/dim]")
```

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass (no regressions from cli.py change).

- [ ] **Step 5: Commit**

```bash
git add tests/test_graph_1a.py src/due_diligence/cli.py
git commit -m "feat: end-to-end graph test + CLI analyze command (Phase 1a)"
```

---

## Task 8: DoD verification

- [ ] **Step 1: Full test suite green**

```bash
uv run pytest -q
```

Expected: all tests pass, 0 failures.

- [ ] **Step 2: Live CLI run against EDGAR** (requires `.env` with real keys)

```bash
uv run due-diligence analyze AAPL
```

Expected output:
```
Due Diligence Analysis — AAPL
Phase 1a: Supervisor + Financials specialist

✓ Run ID: <12-char uuid>
✓ CIK resolved: 0000320193
✓ As of date: 2024-09-28

┌─ Financials — 10-K FY2024 ──────────────────┐
│ Metric          │ Value (USD B) │ Period │ Source     │
│ Revenue         │ 391.0        │ FY2024 │ XBRL       │
│ ...             │ ...          │ ...    │ ...        │
└──────────────────────────────────────────────┘

Specialists pending (Phase 1b): mgmt, filings, market, competitive
```

- [ ] **Step 3: Update `DETAILED_EXPLANATION.md`**

Append a Phase 1a section to `DETAILED_EXPLANATION.md` covering: what LangGraph StateGraph is and how nodes/edges work, what `NotRequired` TypedDict means, why `build_graph(checkpointer=None)` accepts optional checkpointer, what the Supervisor does and why CIK > ticker, the fan-out pattern that will appear in Phase 1b.

- [ ] **Step 4: Update `docs/mce-evolution.md`** — move MCE-03 to Resueltos (done in Task 2 Step 4).

---

## Self-review

**Spec coverage (Phase 1a from README):**

| Requirement | Task |
|---|---|
| `AnalysisState` TypedDict with all fields | Task 3 |
| Supervisor: validate ticker, resolve CIK | Task 5 |
| Financials specialist reuses EdgarAdapter | Task 6 |
| LangGraph StateGraph + SQLite checkpointer | Task 4 + Task 7 |
| Schemas for all agent outputs (stubs) | Task 1 |
| `due-diligence analyze AAPL` CLI command | Task 7 |
| Tests with fixtures, zero real API calls | Tasks 5, 6, 7 |
| MCE-03 agents/CLAUDE.md | Task 2 |

**Placeholder scan:** None found. All steps contain complete code.

**Type consistency:**
- `AnalysisState` defined in Task 3, imported in Tasks 4, 5, 6, 7 — consistent.
- `supervisor_node(state: AnalysisState) -> dict` defined in Task 5, registered in graph Task 4 — consistent.
- `financials_node(state: AnalysisState) -> dict` defined in Task 6, registered in graph Task 4 — consistent.
- `EdgarAdapter.fetch_raw` mocked in Tasks 6 and 7 with same fixture path — consistent.
- `build_graph(checkpointer=None)` defined in Task 4, called with `MemorySaver()` in tests (Task 7) and `SqliteSaver` in CLI (Task 7) — consistent.
