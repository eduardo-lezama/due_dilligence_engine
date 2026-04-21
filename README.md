# Due Diligence Engine

An agentic system for fundamental equity analysis, built on value investing principles. Given a US ticker, it runs a full multi-specialist analysis — from SEC filings to DCF valuation — and delivers a structured verdict with citations, a pre-mortem critique, and an archived report.

> **Status:** Phase 1 complete and verified in production (AAPL, FSLR). Phase 2 (RAG memory + peer comparison) is next.

---

## What it does

```bash
uv run due-diligence analyze AAPL
```

The pipeline:

1. **Supervisor** resolves the ticker to a CIK and routes the analysis
2. **5 specialists run in parallel** (LangGraph `Send()` fan-out):
   - `financials_agent` — XBRL extraction via EDGAR: revenue, FCF, ROIC, margins, balance sheet
   - `management_agent` — DEF 14A, Form 4, 10-K §10: insider ownership, compensation, capital allocation
   - `filings_agent` — 10-K §1/§1A/§7: business description, risk factors, MD&A highlights
   - `market_agent` — Polygon (price, market cap, 52w range) + Finnhub (news)
   - `competitive_agent` — ROIC series + Tavily: moat classification, competitive position
3. **Checkpoint 1 (HITL)** — user reviews moat + management findings, adds context
4. **Valuation agent** — DCF, reverse DCF, EV/EBIT, EV/FCF, P/E, sensitivity table (3×3). Zero LLM numeric generation.
5. **Checkpoint 2 (HITL)** — user sets WACC, terminal growth, projection years
6. **Value Expert** — synthesizes all inputs into a 9-section analysis + BUY/HOLD/SELL verdict with confidence and suggested sizing
7. **Critic agent** — independent pre-mortem: weaknesses, contra-thesis, failure scenarios
8. **Archiver** — saves Markdown report + SQLite row for historical comparison

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

---

## Architecture

```
                     ┌──────────────────────────┐
                     │       SUPERVISOR          │
                     └──────────────┬────────────┘
                                    │ Send() fan-out
     ┌─────────────┬───────────┬────┴─────┬──────────────┐
     ▼             ▼           ▼          ▼              ▼
 Financials    Management   Filings    Market &     Competitive
  (XBRL)      (Form4/DEF14A) (10-K RAG)  News       & Moat
     └─────────────┴───────────┴──────────┴──────────────┘
                                    │ join
                          ┌─────────▼──────────┐
                          │   CHECKPOINT 1     │  ← HITL
                          └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │    VALUATION        │  (tool-lane only)
                          └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │   CHECKPOINT 2     │  ← HITL
                          └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │   VALUE EXPERT      │  (synthesis + verdict)
                          └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │      CRITIC         │  (pre-mortem)
                          └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │     ARCHIVER        │  → report.md + SQLite
                          └────────────────────┘
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
│   ├── agents/          # specialist agents + checkpoint + archiver + value_expert + critic
│   ├── graph/           # LangGraph state, graph builder, edges
│   ├── llm/             # provider-agnostic LLM client (Claude + OpenAI fallback)
│   ├── schemas/         # Pydantic v2 models: reports, base types, citations
│   ├── tools/           # deterministic data adapters (edgar, polygon, finnhub, tavily)
│   ├── config.py        # pydantic-settings: all env vars in one place
│   └── cli.py           # typer CLI entry points
├── tests/
│   └── fixtures/        # cached API responses for offline testing
├── docs/
│   ├── adr/             # Architecture Decision Records (ADR-001 … ADR-012)
│   ├── checklist-value-investing.md
│   └── mce-evolution.md
├── data/
│   ├── reports/         # generated Markdown reports
│   └── analyses.db      # SQLite analysis history
└── DESIGN_DOCUMENT.md   # full project spec, research findings, roadmap detail
```

---

## Roadmap

| Phase | Description | Status |
|---|---|---|
| **0** | Scaffolding, contracts, smoke test | ✅ Complete |
| **1a** | LangGraph skeleton + Supervisor + Financials specialist | ✅ Complete |
| **1b** | 4 parallel specialists + Checkpoint 1 HITL | ✅ Complete |
| **1c** | Valuation + Value Expert + Critic + Archiver + full CLI | ✅ Complete |
| **2** | RAG memory (ChromaDB) + peer ROIC comparison + news ingestion | Planned |
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
