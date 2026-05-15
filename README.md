# Due Diligence Engine

An agentic system for fundamental equity analysis, built on value investing principles. Given a US ticker, it runs a full multi-specialist analysis — from SEC filings to DCF valuation — and delivers a structured verdict with citations, a pre-mortem critique, and an archived report.

> **Status:** Phase 1 + Phase 2 complete, with 8 post-production hardening rounds (Oleadas 1–4, plus MCE-22/23/24/26). The system has been validated against a range of company archetypes beyond the large-cap baseline: small caps, buyback-heavy compounders, stocks in sharp drawdown (with adaptive news coverage and ticker-collision-aware queries), asset-light businesses, companies with amended annual filings, foreign 20-F filers (detect-and-fail-loud), financial firms (detect-and-fail-loud), cyclicals in trough with negative FCF (NOPAT-based DCF fallback), and filers whose debt XBRL bypasses edgartools' curated balance sheet view (raw facts fallback). Qualitative analysis hardened with 4-tier insider activity classification, segment-aware risk analysis, quarterly data integration (8-K Item 2.02 press releases + 10-Q XBRL snapshots), thesis-break self-consistency validation, CAGR qualifiers for atypical periods, backlog/book-to-bill extraction for defense/industrials, and Tavily fallback for earnings call summaries. Peer SIC validation prevents cross-industry comparisons. 254 tests, all green. 26 ADRs documented. Next: Phase 2.5 (re-analysis delta for the same ticker) or Phase 3 (Canadian and European markets).

---

## What it does

```bash
uv run due-diligence analyze AAPL
```

The analysis runs as a pipeline of specialized agents. Each agent has a single responsibility and hands its output to the next.

**Step 1 — Supervisor**
Looks up the company behind the ticker and validates that it's a supported US domestic filer. This is the entry point; nothing runs until it succeeds.

**Step 2 — Financials** *(deterministic, no LLM)*
Pulls the hard numbers from primary sources: revenue trend, profit margins, return on invested capital (ROIC), free cash flow, debt load, and how much the company returns to shareholders through buybacks and dividends — all over a 5-year window. This layer contains zero LLM involvement: the numbers are extracted and computed deterministically from SEC filings.

**Step 3 — Four specialists run in parallel**
Once the financials are ready, four agents work simultaneously:

- **Management** — Investigates who runs the company and whether they're aligned with shareholders. Is the founder still involved? Do executives own meaningful equity? What does the insider transaction history look like — are they buying or selling, and at what prices relative to today? Sources: proxy statement, Form 4 filings, earnings call transcripts.

- **Business & Risk** — Reads the company's own narrative. What does the business actually do, in its own words? What risks does management explicitly flag? What is the outlook — are there signs of business momentum, backlog, guidance, or geographic concentration risk? Source: annual report (10-K).

- **Market & News** — Gathers current market context. What is the stock trading at, what's the 52-week range, what are the material corporate events from the past 30 days — earnings announcements, executive departures, major contracts? For stocks that have fallen sharply, it casts a wider news net to capture tariff, guidance, or regulatory stories that may explain the drawdown. Sources: market data and SEC event filings (8-K).

- **Competitive position & Moat** — Answers the central value-investing question: does this business have a durable competitive advantage? It evaluates whether ROIC has been consistently above cost of capital for multiple years, whether the capital base is stable (not shrinking through asset disposals), and whether there is a credible structural reason competitors can't erode the returns. Moat classification uses a strict four-criteria rubric — "wide moat" requires clear evidence on all four.

**Step 4 — Checkpoint 1** *(you review, you decide)*
The system pauses and presents its findings. It asks pointed, data-grounded questions about conflicts or anomalies — for example, if ROIC is high but insiders are selling aggressively, or if the moat thesis conflicts with margin trends. You also name the peer companies you want compared.

**Step 5 — Peer benchmarking** *(deterministic, no LLM)*
Fetches the same financial metrics for the peers you specified, so ROIC, margins, and growth can be compared side by side on a level playing field.

**Step 6 — Checkpoint 2** *(you set the assumptions)*
Before the valuation runs, the system shows you what the historical growth data implies as an anchor. You input your own assumptions — discount rate, expected growth, time horizon — and explain your reasoning. The system records this.

**Step 7 — Valuation** *(deterministic, no LLM)*
Runs four valuation methods with your inputs: discounted cash flow (DCF), reverse DCF (what growth rate does the current price imply?), and two earnings-based multiples. It also produces a sensitivity table showing intrinsic value across a range of growth and discount rate combinations. Every number is computed, never generated by a language model.

**Step 8 — Value Expert**
Synthesizes everything — financials, management quality, competitive position, market context, peer comparison, and valuation — into a structured 9-section investment thesis. It is required to: adjudicate conflicts between signals, compare the DCF growth assumption against the historical CAGR, name one concrete thesis-breaker (the specific fact that would invalidate the investment case), and cross-check news, insider activity, and margins for value trap patterns. Delivers a BUY / HOLD / SELL verdict with a position size recommendation capped by conviction.

**Step 9 — Critic**
A separate agent reads the Value Expert's thesis with the explicit goal of finding the strongest counter-argument. It looks for DCF assumptions that contradict what the reverse DCF implies, capital return ratios that suggest the business is running on fumes rather than reinvesting, signs of margin compression hidden by headline numbers, and whether a price collapse combined with insider selling is being rationalized away. This agent never sees the Value Expert's conclusion before forming its own assessment.

**Step 10 — Archiver**
Saves the full report — verdict, per-share intrinsic value, upside/downside, citations with source excerpts, peer ROIC table, and corporate events — to a Markdown file and a SQLite database row for historical tracking.

---

## Core design principles

| Principle | What it means in practice |
|---|---|
| **Zero numerical hallucination** | Every number comes from deterministic tools against primary sources (SEC EDGAR XBRL, Polygon). LLMs reason over data, never generate it. |
| **Mandatory verified citations** | Every qualitative claim emits a `Citation{claim, source_url, excerpt, confidence}`. Claims are validated post-hoc by substring-match against the cached filing. Unverified claims are excluded from synthesis. |
| **Provider-agnostic** | Claude primary, OpenAI fallback. No direct `anthropic`/`openai` imports in business code — all LLM access goes through `due_diligence.llm.client`. |
| **Primary sources only** | No `yfinance`, no Yahoo/Morningstar scraping. SEC EDGAR XBRL via `edgartools` is the authoritative source for all financial metrics. |
| **Tools fail loud** | Never a silent fallback from "tool broken" to "LLM invents number". Explicit error + log + branch abort if a critical metric is unavailable. |
| **Socratic HITL** | Checkpoints aren't just approval gates — they carry didactic reasoning to help the user understand what matters and why. |
| **Cyclical peak detection** | DCF warns when the FCF growth rate is capped (raw CAGR > 30%), signaling that the base FCF may be at a cyclical peak and the reverse DCF is more informative. |
| **DCF contradiction flag** | If forward DCF assumes growth >5pp above the reverse-DCF implied growth (i.e. the upside is conditional on growth the market isn't pricing), the system flags it explicitly — the reverse DCF becomes the more informative anchor. |
| **Robust to XBRL variability** | Multiple concept fallbacks for OCF, capex, EBIT, buybacks, dividends, and ROIC denominator. Small caps and asset-light services that don't report the standard `OperatingIncomeLoss` or have negative book equity from buybacks are first-class citizens, not excluded silently. |
| **Adaptive number formatting** | Single-source `fmt_usd()` formatter adapts unit (M/B/k) by magnitude — small caps with $40M FCF show as `$40M`, not `$0.0B`. |
| **US-only scope (Phase 1–2)** | Supports US domestic filers (10-K, XBRL US GAAP). Foreign private issuers (20-F, IFRS ADRs) require Phase 3 adapters. |

---

## Architecture

```mermaid
flowchart TD
    CLI(["`**CLI**
    due-diligence analyze TICKER`"]) --> SUP

    SUP["`**Supervisor**
    ticker → CIK · routing`"] --> FIN

    FIN["`**Financials**
    EDGAR XBRL · ROIC · FCF · CAGR
    _tool-lane_`"]

    FIN -->|Send fan-out| MGT
    FIN -->|Send fan-out| FIL
    FIN -->|Send fan-out| MKT
    FIN -->|Send fan-out| CMP

    MGT["`**Management**
    DEF14A · Form4 · Tavily
    founder · insider pattern
    _LLM + citations_`"]
    FIL["`**Filings RAG**
    10-K §1/§1A/§7
    backlog · guidance · regulatory events
    _LLM + citations_`"]
    MKT["`**Market**
    Polygon · Finnhub · 8-K events
    _tool-lane_`"]
    CMP["`**Competitive & Moat**
    ROIC series · Tavily
    _LLM + citations_`"]

    MGT & FIL & MKT & CMP --> CP1

    CP1{"`**Checkpoint 1**
    Socratic questions · peer prompt
    ← HITL interrupt`"}

    CP1 --> PRS

    PRS["`**peers_node**
    EDGAR XBRL per peer ticker
    injects peer ROIC → competitive
    _tool-lane_`"]

    PRS --> CP2

    CP2{"`**Checkpoint 2**
    CAGR anchor · WACC · growth
    ← HITL interrupt`"}

    CP2 --> VAL

    VAL["`**Valuation**
    DCF · reverse DCF · multiples
    sensitivity table · _tool-lane_`"]

    VAL --> EXP

    EXP["`**Value Expert**
    9-section synthesis · prompt v2
    adjudicate conflicts · thesis-breaker
    BUY / HOLD / SELL + sizing`"]

    EXP --> CRT

    CRT["`**Critic**
    contra-thesis · pre-mortem
    _independent model_`"]

    CRT --> ARC

    ARC["`**Archiver**
    report.md · SQLite row
    citations · peer ROIC · 8-K events`"]

    style CP1 fill:#f5a623,color:#000
    style CP2 fill:#f5a623,color:#000
    style EXP fill:#4a90d9,color:#fff
    style CRT fill:#e74c3c,color:#fff
    style ARC fill:#27ae60,color:#fff
    style PRS fill:#8e44ad,color:#fff
```

---

## Installation

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/eduardo-lezama/due_dilligence_engine.git
cd due_dilligence_engine
uv sync
cp .env.example .env   # fill in your API keys
```

### Required API keys (`.env`)

| Variable | Source | Free tier |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | — |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) | — |
| `POLYGON_API_KEY` | [polygon.io](https://polygon.io) | 5 req/min |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io) | yes |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) | 1000 req/month |

---

## Usage

```bash
# Full analysis with interactive checkpoints
uv run due-diligence analyze AAPL

# US domestic tickers only — foreign ADRs (20-F filers) not supported yet
# ✅ AAPL, FSLR, ACMR, CALM  ❌ GRVY, BABA, TSM (foreign private issuers)

# Run smoke tests (no real API calls — uses cached fixtures)
uv run pytest tests/test_smoke.py -q

# Full test suite
uv run pytest -q
```

### Output

Each analysis produces:
- **Rich CLI report** with verdict, per-share intrinsic value, and upside/downside
- **Markdown file** at `data/reports/<TICKER>-<DATE>.md`
- **SQLite row** at `data/analyses.db` (ticker, verdict, confidence, DCF upside, moat classification)

---

## Project structure

```
due_dilligence_engine/
├── src/due_diligence/
│   ├── agents/          # specialist agents, peers_node, checkpoint, value_expert, critic, archiver
│   ├── graph/           # LangGraph state (AnalysisState), graph builder, edges
│   ├── llm/             # provider-agnostic LLM client (Claude primary, OpenAI fallback)
│   ├── schemas/         # Pydantic v2 models: reports, base types, citations
│   ├── tools/           # deterministic data adapters (edgar, polygon, finnhub, tavily)
│   ├── utils/           # shared helpers — single source of truth for cross-cutting concerns (formatting, etc.)
│   ├── config.py        # pydantic-settings: all env vars in one place
│   └── cli.py           # typer CLI: interactive checkpoints, Socratic questions
├── tests/
│   └── fixtures/        # cached API responses for offline testing (230 tests, zero real API calls)
├── docs/
│   ├── adr/             # Architecture Decision Records (ADR-001 … ADR-024)
│   ├── checklist-value-investing.md
│   ├── mce-evolution.md # deferred work with verifiable triggers
│   └── phase-2-strategic-review.md
├── data/
│   ├── reports/         # generated Markdown reports
│   └── analyses.db      # SQLite analysis history
├── CONCISE_EXPLANATION.md  # architecture study guide (recommended read)
├── AGENTIC_PATTERNS.md     # transferable agentic engineering patterns + anti-patterns
├── DETAILED_EXPLANATION.md # full didactic log of all implementation decisions
└── DESIGN_DOCUMENT.md      # original project spec and research findings
```

---

## Roadmap

| Phase | Description | Status |
|---|---|---|
| **0** | Scaffolding, contracts, smoke test | ✅ Complete |
| **1a** | LangGraph skeleton + Supervisor + Financials specialist | ✅ Complete |
| **1b** | 4 parallel specialists + Checkpoint 1 HITL | ✅ Complete |
| **1c** | Valuation + Value Expert + Critic + Archiver + full CLI | ✅ Complete |
| **2** | Peer ROIC (user-supplied), earnings transcripts, 8-K events, Expert prompt v2, Socratic checkpoints | ✅ Complete |
| **2.5** | RAG memory — re-analysis delta for same ticker (ADR-013, ChromaDB) | Planned |
| **3** | Multi-market: CAD (SEDAR+) + EU (ESMA/CNMV) | Planned |
| **4** | Screener module (quantitative filter → analyst pipeline) | Planned |
| **5** | Web app (FastAPI + React/Streamlit, Docker, NAS deploy) | Planned |

See [`DESIGN_DOCUMENT.md`](DESIGN_DOCUMENT.md) for detailed phase specs, research findings, and all ADRs.

---

## Tech stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Package management | `uv` |
| Agentic orchestration | LangGraph (state, interrupt, checkpointer) |
| LLM access | Anthropic SDK + OpenAI SDK via provider-agnostic wrapper |
| Schema validation | Pydantic v2 |
| SEC filings | `edgartools` (XBRL, primary source) |
| Market data | `polygon-api-client` (prices, market cap) |
| Transcripts / news | `finnhub-python` |
| Web search | Tavily |
| CLI | `typer` + `rich` |
| Storage | SQLite + filesystem |
| Testing | pytest + cached fixtures (zero real API calls) |
| Logging | `structlog` (structured JSON) |

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
