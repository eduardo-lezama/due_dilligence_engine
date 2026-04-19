# Phase 0 — Scaffolding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the project skeleton with validated Pydantic schemas, a provider-agnostic LLM client, a working Edgar XBRL adapter, and a passing smoke test — zero hallucination architecture established from day 0.

**Architecture:** Src-layout Python package (`src/due_diligence/`) with three sub-modules: `schemas/` (Pydantic models), `llm/` (provider wrapper), `tools/` (data adapters). All numerical data flows through tools only — LLM client is wired up but not called in Phase 0 tests. MCE-triggered CLAUDE.md files are created together with their directories.

**Tech Stack:** Python 3.12, uv, Pydantic v2, pydantic-settings, edgartools, anthropic SDK, openai SDK, typer, rich, structlog, pytest, pytest-mock.

---

## File map

| Path | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Create | Project metadata, deps, entry points |
| `.gitignore` | Create | Exclude `.env`, `data/`, `__pycache__`, `.venv` |
| `.env.example` | Create | Required env vars with placeholder values |
| `src/due_diligence/__init__.py` | Create | Package root |
| `src/due_diligence/CLAUDE.md` | Create | MCE-01: code conventions for this package |
| `src/due_diligence/config.py` | Create | pydantic-settings Settings class |
| `src/due_diligence/schemas/__init__.py` | Create | Re-exports |
| `src/due_diligence/schemas/base.py` | Create | Citation, Claim, MetricValue, FinancialsReport |
| `src/due_diligence/llm/__init__.py` | Create | Re-exports |
| `src/due_diligence/llm/CLAUDE.md` | Create | MCE-04: LLM wrapper conventions |
| `src/due_diligence/llm/client.py` | Create | LLMClient + get_client() singleton |
| `src/due_diligence/tools/__init__.py` | Create | Re-exports |
| `src/due_diligence/tools/CLAUDE.md` | Create | MCE-02: adapter conventions |
| `src/due_diligence/tools/edgar.py` | Create | EdgarAdapter: fetch_raw + parse_financials |
| `src/due_diligence/cli.py` | Create | Typer app with `get-financials` sub-command |
| `tests/__init__.py` | Create | Package marker |
| `tests/CLAUDE.md` | Create | MCE-05: fixture-first testing conventions |
| `tests/conftest.py` | Create | Shared fixtures (settings override, fixture loader) |
| `tests/fixtures/aapl_xbrl_raw.json` | Create | Cached XBRL data for AAPL (FY2024) |
| `tests/test_schemas.py` | Create | Pydantic model instantiation + validation |
| `tests/test_llm_client.py` | Create | LLMClient with mocked providers + fallback |
| `tests/test_edgar.py` | Create | parse_financials() with fixture |
| `tests/test_smoke.py` | Create | End-to-end: fixture → FinancialsReport shape |

---

## Task 1: Repo skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/due_diligence/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p src/due_diligence/schemas src/due_diligence/llm src/due_diligence/tools tests/fixtures data/reports
touch src/due_diligence/__init__.py src/due_diligence/schemas/__init__.py
touch src/due_diligence/llm/__init__.py src/due_diligence/tools/__init__.py
touch tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "due-diligence"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=0.50.0",
    "openai>=1.50.0",
    "langgraph>=0.2.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "edgartools>=3.0.0",
    "polygon-api-client>=1.0.0",
    "finnhub-python>=2.0.0",
    "typer[all]>=0.12.0",
    "rich>=13.0.0",
    "structlog>=24.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.14.0",
]

[project.scripts]
due-diligence = "due_diligence.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/due_diligence"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
src = ["src"]
line-length = 100
target-version = "py312"
```

- [ ] **Step 3: Write `.gitignore`**

```
.env
.venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
data/
*.db
dist/
build/
*.egg-info/
.mypy_cache/
.ruff_cache/
```

- [ ] **Step 4: Write `.env.example`**

```
# Anthropic (required)
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI fallback (optional but recommended)
OPENAI_API_KEY=sk-...

# SEC EDGAR identity (required by SEC — "Name email@example.com")
SEC_USER_AGENT=Eduardo Lezama eduardo.lezama@basetis.com

# Data source API keys (optional in Phase 0, required Phase 1)
POLYGON_API_KEY=
FINNHUB_API_KEY=

# Model defaults
DEFAULT_MODEL=claude-sonnet-4-6
OPENAI_FALLBACK_MODEL=gpt-4o-mini

# Storage paths (relative to project root)
DB_PATH=data/analyses.db
REPORTS_DIR=data/reports
```

- [ ] **Step 5: Install dependencies**

```bash
uv sync --extra dev
```

Expected: resolves and installs all packages, no errors. If a package version doesn't exist yet, adjust the version constraint (e.g. drop `>=3.0.0` to `>=2.0.0` for edgartools).

- [ ] **Step 6: Verify import works**

```bash
uv run python -c "import due_diligence; print('ok')"
```

Expected output: `ok`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/ tests/
git commit -m "chore: initial project skeleton with uv and src layout"
```

---

## Task 2: MCE gate — subdirectory CLAUDE.md files (MCE-01, 02, 04, 05)

**Files:**
- Create: `src/due_diligence/CLAUDE.md`
- Create: `src/due_diligence/llm/CLAUDE.md`
- Create: `src/due_diligence/tools/CLAUDE.md`
- Create: `tests/CLAUDE.md`

These are structural context files, not code. No test step. Create them together since all four directories now exist.

- [ ] **Step 1: Write `src/due_diligence/CLAUDE.md` (MCE-01)**

```markdown
# CLAUDE.md — due_diligence package

Code conventions for `src/due_diligence/`. Complements root CLAUDE.md.

## Module structure
- `config.py` — Settings (pydantic-settings). Only place that reads env vars.
- `schemas/` — Pydantic v2 models. Zero business logic, only data shape.
- `llm/` — Provider wrapper. Only place that imports `anthropic` or `openai`.
- `tools/` — Deterministic data adapters. Never call LLM from here.
- `cli.py` — Typer entry points. Thin: calls tools/agents, formats with rich.

## Invariants
- All module outputs are Pydantic v2 models. No bare `dict` returns.
- `from due_diligence.llm.client import get_client` — never import anthropic/openai directly.
- `from due_diligence.config import settings` — never read env vars directly.
- Async functions use `async def` + `await`. Don't mix sync/async in the same call chain.
- structlog for all logging: `log = structlog.get_logger()`.

## Naming
- Adapters: `*Adapter` (e.g. `EdgarAdapter`).
- Providers: `*Provider` (interface) + `*Adapter` (implementation).
- Schemas: PascalCase Pydantic models in `schemas/base.py`.
- Tools (public API): `get_*`, `fetch_*` snake_case functions.
```

- [ ] **Step 2: Write `src/due_diligence/llm/CLAUDE.md` (MCE-04)**

```markdown
# CLAUDE.md — llm/

Provider wrapper. The only module that imports `anthropic` or `openai`.

## Rules
- `client.py` exports `get_client() -> LLMClient` and `LLMClient.complete()`.
- Never raise a provider-specific exception outside this module — catch and re-raise as `LLMError`.
- Fallback order: Claude primary → OpenAI. Log the fallback with structlog at WARNING.
- Model selection: always accept an explicit `model` param; fall back to `settings.default_model`.
- Prompt caching (Anthropic): use `cache_control` blocks when system prompt > 1024 tokens. Mark the checklist and verdict rubric as cacheable.
- Cost tracking: log `input_tokens`, `output_tokens`, `model` on every call at DEBUG level.

## Do not add here
- Business logic (that belongs in agents/).
- Retry logic beyond 2 attempts + fallback (YAGNI).
- Streaming (deferred until Phase 1 CLI needs it).
```

- [ ] **Step 3: Write `src/due_diligence/tools/CLAUDE.md` (MCE-02)**

```markdown
# CLAUDE.md — tools/

Deterministic data adapters. Zero LLM calls. Numbers only from here.

## Rules
- Every adapter implements a `fetch_raw(ticker) -> dict` (network call) + `parse_*(raw) -> PydanticModel` (pure function, testable with fixtures).
- Tests MUST mock `fetch_raw` or use fixture files in `tests/fixtures/`. Zero real API calls in pytest.
- Fail loud: if a required metric is missing from source, raise `DataUnavailableError` with ticker + field. Never return `None` silently for a required metric.
- Always set `currency_code` on MetricValue. Never assume USD without explicit XBRL tag.
- Cache raw API responses in `tests/fixtures/<ticker>_<source>_raw.json` when running integration tests (mark with `@pytest.mark.integration`).

## Adapters planned
- `edgar.py` — SEC EDGAR XBRL via edgartools (primary, Phase 0-1).
- `polygon.py` — OHLCV prices via polygon-api-client (Phase 1).
- `finnhub.py` — earnings call transcripts via finnhub-python (Phase 1).
```

- [ ] **Step 4: Write `tests/CLAUDE.md` (MCE-05)**

```markdown
# CLAUDE.md — tests/

Fixture-first testing. Zero real API calls in the default test run.

## Rules
- Every test that touches a network adapter MUST use a fixture from `tests/fixtures/`.
- Fixture files are JSON. Name: `<ticker>_<source>_raw.json` (e.g. `aapl_xbrl_raw.json`).
- Integration tests (real network) use `@pytest.mark.integration` and are excluded from the default `pytest -q` run. Run them manually: `pytest -m integration`.
- Test files: `test_<module>.py` mirroring `src/due_diligence/<module>.py`.
- conftest.py: shared fixtures only (settings override, fixture loader helper). No business logic.
- Use `pytest-mock` (`mocker` fixture) for mocking. No `unittest.mock` directly.
- A test that only checks "no exception raised" is not a test — assert the shape of the output.
```

- [ ] **Step 5: Commit**

```bash
git add src/due_diligence/CLAUDE.md src/due_diligence/llm/CLAUDE.md src/due_diligence/tools/CLAUDE.md tests/CLAUDE.md
git commit -m "docs: MCE-triggered CLAUDE.md files for new subdirectories (MCE-01 02 04 05)"
```

---

## Task 3: Config module

**Files:**
- Create: `src/due_diligence/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError


def test_settings_loads_required_fields(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")

    # Re-import to pick up monkeypatched env (settings is a module-level singleton,
    # so we reload the module to force re-instantiation)
    import importlib
    import due_diligence.config as cfg_module
    importlib.reload(cfg_module)

    assert cfg_module.settings.anthropic_api_key == "sk-test-key"
    assert cfg_module.settings.sec_user_agent == "Test User test@example.com"
    assert cfg_module.settings.default_model == "claude-sonnet-4-6"
    assert cfg_module.settings.openai_fallback_model == "gpt-4o-mini"


def test_settings_missing_required_field_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    import importlib
    import due_diligence.config as cfg_module

    with pytest.raises((ValidationError, Exception)):
        importlib.reload(cfg_module)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'due_diligence.config'`

- [ ] **Step 3: Write `src/due_diligence/config.py`**

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    anthropic_api_key: str = Field(..., validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY")
    sec_user_agent: str = Field(..., validation_alias="SEC_USER_AGENT")

    polygon_api_key: str = Field("", validation_alias="POLYGON_API_KEY")
    finnhub_api_key: str = Field("", validation_alias="FINNHUB_API_KEY")

    default_model: str = Field("claude-sonnet-4-6", validation_alias="DEFAULT_MODEL")
    openai_fallback_model: str = Field("gpt-4o-mini", validation_alias="OPENAI_FALLBACK_MODEL")

    db_path: str = Field("data/analyses.db", validation_alias="DB_PATH")
    reports_dir: str = Field("data/reports", validation_alias="REPORTS_DIR")


settings = Settings()
```

- [ ] **Step 4: Create a `.env` file for local development**

Copy `.env.example` to `.env` and fill in real values:

```bash
cp .env.example .env
# Edit .env — add real ANTHROPIC_API_KEY and SEC_USER_AGENT at minimum
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS — both tests green.

- [ ] **Step 6: Commit**

```bash
git add src/due_diligence/config.py tests/test_config.py
git commit -m "feat: add Settings via pydantic-settings with env validation"
```

---

## Task 4: Pydantic schemas

**Files:**
- Create: `src/due_diligence/schemas/base.py`
- Modify: `src/due_diligence/schemas/__init__.py`
- Create: `tests/test_schemas.py`

**What these models are:**
- `Citation` — a qualitative claim with a verified source excerpt. `confidence=0.0` means unverified.
- `Claim` — a statement that may or may not have a citation yet.
- `MetricValue` — a single numeric datapoint with period, currency, source, and GAAP flag.
- `FinancialsReport` — complete financial picture for one company/period. This is a stub in Phase 0 (only the fields used by the Edgar adapter are required).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schemas.py`:

```python
from datetime import date
import pytest
from pydantic import ValidationError


def test_citation_valid():
    from due_diligence.schemas.base import Citation
    c = Citation(
        claim="Revenue grew 8% YoY",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
        excerpt="Net sales were $391.0 billion, an increase of 2 percent compared to the prior year.",
        confidence=0.95,
    )
    assert c.confidence == 0.95
    assert c.gaap_vs_nongaap_flag is None


def test_citation_unverified_has_zero_confidence():
    from due_diligence.schemas.base import Citation
    c = Citation(
        claim="Some claim",
        source_url="https://example.com",
        excerpt="",
        confidence=0.0,
    )
    assert c.confidence == 0.0


def test_citation_confidence_out_of_range_raises():
    from due_diligence.schemas.base import Citation
    with pytest.raises(ValidationError):
        Citation(
            claim="x",
            source_url="https://example.com",
            excerpt="x",
            confidence=1.5,  # invalid
        )


def test_metric_value_valid():
    from due_diligence.schemas.base import MetricValue
    m = MetricValue(
        value=391_035_000_000.0,
        unit="USD",
        period="FY2024",
        source="XBRL",
        currency_code="USD",
        gaap_vs_nongaap="GAAP",
    )
    assert m.value == 391_035_000_000.0


def test_financials_report_stub():
    from due_diligence.schemas.base import FinancialsReport, MetricValue
    from datetime import date

    def _mv(value: float, period: str) -> MetricValue:
        return MetricValue(value=value, unit="USD", period=period, source="XBRL")

    report = FinancialsReport(
        ticker="AAPL",
        cik="0000320193",
        as_of_date=date(2024, 9, 28),
        revenue=[_mv(391_035e6, "FY2024"), _mv(383_285e6, "FY2023")],
        gross_profit=[_mv(180_683e6, "FY2024")],
        operating_income=[_mv(123_216e6, "FY2024")],
        net_income=[_mv(93_736e6, "FY2024")],
        operating_cash_flow=[_mv(118_254e6, "FY2024")],
        capex=[_mv(9_447e6, "FY2024")],
        free_cash_flow=[_mv(108_807e6, "FY2024")],
        total_assets=_mv(364_980e6, "FY2024"),
        total_debt=_mv(101_304e6, "FY2024"),
        cash_and_equivalents=_mv(65_171e6, "FY2024"),
        shares_outstanding=MetricValue(
            value=15_204_137_000.0, unit="shares", period="FY2024", source="XBRL"
        ),
        source_filing="10-K FY2024",
    )
    assert report.ticker == "AAPL"
    assert len(report.revenue) == 2
    assert report.free_cash_flow[0].value == pytest.approx(108_807e6)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_schemas.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'due_diligence.schemas.base'`

- [ ] **Step 3: Write `src/due_diligence/schemas/base.py`**

```python
from datetime import date
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class Citation(BaseModel):
    claim: str
    source_url: str
    excerpt: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    gaap_vs_nongaap_flag: Optional[str] = None  # "GAAP", "Non-GAAP", or None


class Claim(BaseModel):
    text: str
    citation: Optional[Citation] = None
    verified: bool = False


class MetricValue(BaseModel):
    value: float
    unit: str = "USD"
    period: str
    source: str  # "XBRL", "calculated", "10-K", etc.
    currency_code: str = "USD"
    gaap_vs_nongaap: str = "GAAP"


class FinancialsReport(BaseModel):
    ticker: str
    cik: str
    as_of_date: date

    # Income statement (list = multi-year, index 0 = most recent)
    revenue: list[MetricValue]
    gross_profit: list[MetricValue]
    operating_income: list[MetricValue]
    net_income: list[MetricValue]

    # Cash flow
    operating_cash_flow: list[MetricValue]
    capex: list[MetricValue]
    free_cash_flow: list[MetricValue]

    # Balance sheet (point-in-time, most recent)
    total_assets: MetricValue
    total_debt: MetricValue
    cash_and_equivalents: MetricValue

    # Share data
    shares_outstanding: MetricValue

    # Meta
    source_filing: str
    citations: list[Citation] = Field(default_factory=list)
```

- [ ] **Step 4: Update `src/due_diligence/schemas/__init__.py`**

```python
from due_diligence.schemas.base import (
    Citation,
    Claim,
    MetricValue,
    FinancialsReport,
)

__all__ = ["Citation", "Claim", "MetricValue", "FinancialsReport"]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_schemas.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/due_diligence/schemas/ tests/test_schemas.py
git commit -m "feat: Pydantic v2 base schemas — Citation, Claim, MetricValue, FinancialsReport"
```

---

## Task 5: LLM client

**Files:**
- Create: `src/due_diligence/llm/client.py`
- Modify: `src/due_diligence/llm/__init__.py`
- Create: `tests/test_llm_client.py`

**What this is:** A thin wrapper that accepts `messages + system + model` and returns `str`. It tries Anthropic first; if that raises any exception, it falls back to OpenAI. Business logic never imports `anthropic` or `openai` directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_client.py`:

```python
import pytest
from unittest.mock import MagicMock, patch


def test_complete_uses_anthropic_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="mocked answer")]

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        from due_diligence.llm.client import LLMClient
        client = LLMClient()
        result = client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-haiku-4-5-20251001",
        )

    assert result == "mocked answer"
    mock_client.messages.create.assert_called_once()


def test_complete_falls_back_to_openai_on_anthropic_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")
    monkeypatch.setenv("SEC_USER_AGENT", "Test test@example.com")

    mock_oai_choice = MagicMock()
    mock_oai_choice.message.content = "openai answer"
    mock_oai_response = MagicMock()
    mock_oai_response.choices = [mock_oai_choice]

    with patch("anthropic.Anthropic") as mock_ant, patch("openai.OpenAI") as mock_oai_cls:
        mock_ant_client = MagicMock()
        mock_ant_client.messages.create.side_effect = Exception("rate limit")
        mock_ant.return_value = mock_ant_client

        mock_oai_client = MagicMock()
        mock_oai_client.chat.completions.create.return_value = mock_oai_response
        mock_oai_cls.return_value = mock_oai_client

        from due_diligence.llm.client import LLMClient
        client = LLMClient()
        result = client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-sonnet-4-6",
        )

    assert result == "openai answer"
    mock_oai_client.chat.completions.create.assert_called_once()


def test_get_client_returns_singleton():
    import importlib
    import due_diligence.llm.client as mod
    importlib.reload(mod)

    c1 = mod.get_client()
    c2 = mod.get_client()
    assert c1 is c2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_llm_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'due_diligence.llm.client'`

- [ ] **Step 3: Write `src/due_diligence/llm/client.py`**

```python
from typing import Optional
import structlog

from due_diligence.config import settings

log = structlog.get_logger()


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._anthropic = None
        self._openai = None

    def _get_anthropic(self):
        if self._anthropic is None:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._anthropic

    def _get_openai(self):
        if self._openai is None:
            import openai
            self._openai = openai.OpenAI(api_key=settings.openai_api_key)
        return self._openai

    def complete(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        model = model or settings.default_model
        try:
            result = self._complete_anthropic(messages, model, system, max_tokens)
            log.debug("llm_complete", provider="anthropic", model=model)
            return result
        except Exception as e:
            log.warning("anthropic_failed_falling_back", error=str(e), model=model)
            return self._complete_openai(messages, system, max_tokens)

    def _complete_anthropic(
        self, messages: list[dict], model: str, system: Optional[str], max_tokens: int
    ) -> str:
        client = self._get_anthropic()
        kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = client.messages.create(**kwargs)
        return response.content[0].text

    def _complete_openai(
        self, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
        client = self._get_openai()
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)
        response = client.chat.completions.create(
            model=settings.openai_fallback_model,
            messages=oai_messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content


_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
```

- [ ] **Step 4: Update `src/due_diligence/llm/__init__.py`**

```python
from due_diligence.llm.client import LLMClient, LLMError, get_client

__all__ = ["LLMClient", "LLMError", "get_client"]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_llm_client.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/due_diligence/llm/ tests/test_llm_client.py
git commit -m "feat: provider-agnostic LLM client with Claude primary / OpenAI fallback"
```

---

## Task 6: Edgar adapter

**Files:**
- Create: `src/due_diligence/tools/edgar.py`
- Modify: `src/due_diligence/tools/__init__.py`
- Create: `tests/fixtures/aapl_xbrl_raw.json`
- Create: `tests/conftest.py`
- Create: `tests/test_edgar.py`

**What this is:** Two-layer adapter.
- `EdgarAdapter.fetch_raw(ticker) -> dict` — network call to EDGAR (real data, used in integration tests and the DoD CLI check).
- `EdgarAdapter.parse_financials(raw: dict) -> FinancialsReport` — pure function, used in unit tests with fixture.

The fixture contains pre-fetched AAPL FY2024 data (hardcoded real values — see below).

**Note on edgartools API:** Before implementing `fetch_raw`, verify the exact method signatures with:
```bash
uv run python -c "from edgar import Company; help(Company.get_facts)"
```
The parse logic below targets the `company_facts` JSON structure (same as SEC EDGAR `/api/xbrl/companyfacts/` endpoint). Adjust field names if the installed version differs.

- [ ] **Step 1: Create the fixture file**

Create `tests/fixtures/aapl_xbrl_raw.json` with this content (real AAPL FY2024 values from 10-K):

```json
{
  "ticker": "AAPL",
  "cik": "0000320193",
  "as_of_date": "2024-09-28",
  "source_filing": "10-K FY2024",
  "annual": {
    "revenue": [
      {"year": 2024, "value": 391035000000, "period": "FY2024"},
      {"year": 2023, "value": 383285000000, "period": "FY2023"},
      {"year": 2022, "value": 394328000000, "period": "FY2022"},
      {"year": 2021, "value": 365817000000, "period": "FY2021"},
      {"year": 2020, "value": 274515000000, "period": "FY2020"}
    ],
    "gross_profit": [
      {"year": 2024, "value": 180683000000, "period": "FY2024"},
      {"year": 2023, "value": 169148000000, "period": "FY2023"}
    ],
    "operating_income": [
      {"year": 2024, "value": 123216000000, "period": "FY2024"},
      {"year": 2023, "value": 114301000000, "period": "FY2023"}
    ],
    "net_income": [
      {"year": 2024, "value": 93736000000, "period": "FY2024"},
      {"year": 2023, "value": 96995000000, "period": "FY2023"}
    ],
    "operating_cash_flow": [
      {"year": 2024, "value": 118254000000, "period": "FY2024"},
      {"year": 2023, "value": 110543000000, "period": "FY2023"}
    ],
    "capex": [
      {"year": 2024, "value": 9447000000, "period": "FY2024"},
      {"year": 2023, "value": 10959000000, "period": "FY2023"}
    ]
  },
  "balance_sheet": {
    "total_assets": {"value": 364980000000, "period": "FY2024"},
    "long_term_debt": {"value": 85750000000, "period": "FY2024"},
    "short_term_debt": {"value": 10912000000, "period": "FY2024"},
    "cash_and_equivalents": {"value": 29943000000, "period": "FY2024"},
    "short_term_investments": {"value": 35228000000, "period": "FY2024"}
  },
  "shares": {
    "shares_outstanding": {"value": 15115823000, "period": "FY2024"}
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_edgar.py`:

```python
import json
import pytest
from pathlib import Path
from datetime import date


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json"


@pytest.fixture
def aapl_raw():
    return json.loads(FIXTURE_PATH.read_text())


def test_parse_financials_returns_valid_report(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)

    from due_diligence.schemas.base import FinancialsReport
    assert isinstance(report, FinancialsReport)


def test_parse_financials_ticker_and_cik(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.ticker == "AAPL"
    assert report.cik == "0000320193"


def test_parse_financials_revenue_values(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)

    assert len(report.revenue) >= 2
    assert report.revenue[0].value == pytest.approx(391_035_000_000)
    assert report.revenue[0].period == "FY2024"
    assert report.revenue[0].source == "XBRL"


def test_parse_financials_free_cash_flow_is_computed(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)

    # FCF = OCF - capex
    expected_fcf = 118_254_000_000 - 9_447_000_000
    assert report.free_cash_flow[0].value == pytest.approx(expected_fcf)
    assert report.free_cash_flow[0].source == "calculated"


def test_parse_financials_total_debt_is_sum_of_lt_and_st(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)

    expected_debt = 85_750_000_000 + 10_912_000_000
    assert report.total_debt.value == pytest.approx(expected_debt)


def test_parse_financials_cash_includes_short_term_investments(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)

    # Cash = cash_and_equivalents + short_term_investments
    expected_cash = 29_943_000_000 + 35_228_000_000
    assert report.cash_and_equivalents.value == pytest.approx(expected_cash)


def test_parse_financials_source_filing(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.source_filing == "10-K FY2024"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_edgar.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'due_diligence.tools.edgar'`

- [ ] **Step 4: Write `src/due_diligence/tools/edgar.py`**

```python
from __future__ import annotations

from datetime import date
from typing import Any
import structlog

from due_diligence.config import settings
from due_diligence.schemas.base import FinancialsReport, MetricValue

log = structlog.get_logger()


class DataUnavailableError(Exception):
    pass


class EdgarAdapter:
    @staticmethod
    def fetch_raw(ticker: str) -> dict[str, Any]:
        """Live EDGAR fetch. Never called in unit tests — use parse_financials with fixture."""
        from edgar import Company, set_identity  # type: ignore[import]

        set_identity(settings.sec_user_agent)
        log.info("edgar_fetch_start", ticker=ticker)

        company = Company(ticker)
        facts = company.get_facts()

        # Extract multi-year annual data from XBRL company facts
        def _extract_annual(concept: str, years: int = 5) -> list[dict]:
            try:
                df = getattr(facts.us_gaap, concept).to_dataframe()
                annual = df[df["form"].isin(["10-K", "10-K/A"])].copy()
                annual = annual.sort_values("end", ascending=False).head(years)
                return [
                    {
                        "year": row["end"].year,
                        "value": float(row["val"]),
                        "period": f"FY{row['end'].year}",
                    }
                    for _, row in annual.iterrows()
                ]
            except AttributeError:
                raise DataUnavailableError(f"{ticker}: XBRL concept '{concept}' not found")

        def _extract_point(concept: str, period: str) -> dict:
            try:
                df = getattr(facts.us_gaap, concept).to_dataframe()
                latest = df[df["form"].isin(["10-K", "10-K/A"])].sort_values("end").iloc[-1]
                return {"value": float(latest["val"]), "period": period}
            except (AttributeError, IndexError):
                raise DataUnavailableError(f"{ticker}: XBRL concept '{concept}' not found")

        # Determine fiscal year end from most recent 10-K
        filings = company.get_filings(form="10-K")
        latest_10k = filings.latest()
        fy_end = str(latest_10k.period_of_report)
        fy_label = f"FY{fy_end[:4]}"

        revenue = _extract_annual("RevenueFromContractWithCustomerExcludingAssessedTax")
        if not revenue:
            revenue = _extract_annual("Revenues")

        lt_debt = _extract_point("LongTermDebt", fy_label)
        st_debt_value = 0.0
        try:
            st_debt = _extract_point("ShortTermBorrowings", fy_label)
            st_debt_value = st_debt["value"]
        except DataUnavailableError:
            pass

        cash = _extract_point("CashAndCashEquivalentsAtCarryingValue", fy_label)
        sti_value = 0.0
        try:
            sti = _extract_point("AvailableForSaleSecuritiesDebtSecuritiesCurrent", fy_label)
            sti_value = sti["value"]
        except DataUnavailableError:
            pass

        raw = {
            "ticker": ticker.upper(),
            "cik": str(company.cik).zfill(10),
            "as_of_date": fy_end,
            "source_filing": f"10-K {fy_label}",
            "annual": {
                "revenue": revenue,
                "gross_profit": _extract_annual("GrossProfit"),
                "operating_income": _extract_annual("OperatingIncomeLoss"),
                "net_income": _extract_annual("NetIncomeLoss"),
                "operating_cash_flow": _extract_annual("NetCashProvidedByUsedInOperatingActivities"),
                "capex": _extract_annual("PaymentsToAcquirePropertyPlantAndEquipment"),
            },
            "balance_sheet": {
                "total_assets": _extract_point("Assets", fy_label),
                "long_term_debt": lt_debt,
                "short_term_debt": {"value": st_debt_value, "period": fy_label},
                "cash_and_equivalents": cash,
                "short_term_investments": {"value": sti_value, "period": fy_label},
            },
            "shares": {
                "shares_outstanding": _extract_point("CommonStockSharesOutstanding", fy_label),
            },
        }

        log.info("edgar_fetch_complete", ticker=ticker, fy=fy_label)
        return raw

    @staticmethod
    def parse_financials(raw: dict[str, Any]) -> FinancialsReport:
        """Pure function — transforms raw dict into validated FinancialsReport."""
        as_of = date.fromisoformat(raw["as_of_date"])

        def _mv_list(items: list[dict], source: str = "XBRL") -> list[MetricValue]:
            return [
                MetricValue(value=i["value"], unit="USD", period=i["period"], source=source)
                for i in items
            ]

        def _mv(item: dict, source: str = "XBRL") -> MetricValue:
            return MetricValue(value=item["value"], unit="USD", period=item["period"], source=source)

        annual = raw["annual"]
        bs = raw["balance_sheet"]
        shares = raw["shares"]

        ocf_list = _mv_list(annual["operating_cash_flow"])
        capex_list = _mv_list(annual["capex"])
        fcf_list = [
            MetricValue(
                value=ocf.value - cap.value,
                unit="USD",
                period=ocf.period,
                source="calculated",
            )
            for ocf, cap in zip(ocf_list, capex_list)
        ]

        total_debt_value = bs["long_term_debt"]["value"] + bs["short_term_debt"]["value"]
        cash_value = bs["cash_and_equivalents"]["value"] + bs["short_term_investments"]["value"]
        period = bs["total_assets"]["period"]

        return FinancialsReport(
            ticker=raw["ticker"],
            cik=raw["cik"],
            as_of_date=as_of,
            revenue=_mv_list(annual["revenue"]),
            gross_profit=_mv_list(annual["gross_profit"]),
            operating_income=_mv_list(annual["operating_income"]),
            net_income=_mv_list(annual["net_income"]),
            operating_cash_flow=ocf_list,
            capex=capex_list,
            free_cash_flow=fcf_list,
            total_assets=_mv(bs["total_assets"]),
            total_debt=MetricValue(value=total_debt_value, unit="USD", period=period, source="XBRL"),
            cash_and_equivalents=MetricValue(value=cash_value, unit="USD", period=period, source="XBRL"),
            shares_outstanding=MetricValue(
                value=shares["shares_outstanding"]["value"],
                unit="shares",
                period=shares["shares_outstanding"]["period"],
                source="XBRL",
            ),
            source_filing=raw["source_filing"],
        )


def get_financials(ticker: str) -> FinancialsReport:
    raw = EdgarAdapter.fetch_raw(ticker)
    return EdgarAdapter.parse_financials(raw)
```

- [ ] **Step 5: Update `src/due_diligence/tools/__init__.py`**

```python
from due_diligence.tools.edgar import EdgarAdapter, get_financials, DataUnavailableError

__all__ = ["EdgarAdapter", "get_financials", "DataUnavailableError"]
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_edgar.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/due_diligence/tools/ tests/test_edgar.py tests/fixtures/aapl_xbrl_raw.json
git commit -m "feat: EdgarAdapter with XBRL parser and fixture-based tests"
```

---

## Task 7: CLI entry point + smoke test

**Files:**
- Create: `src/due_diligence/cli.py`
- Create: `tests/test_smoke.py`

**What this is:** A minimal Typer CLI with a single `get-financials` sub-command that calls `get_financials()` (live EDGAR) and prints a `rich` table of key metrics. The smoke test verifies the same flow with the fixture, confirming the full pipeline `fixture → parse → report` is wired end-to-end.

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_smoke.py`:

```python
import json
from pathlib import Path
import pytest


def test_smoke_financials_pipeline_from_fixture():
    """Full pipeline: fixture dict → EdgarAdapter.parse → FinancialsReport shape check."""
    fixture_path = Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json"
    raw = json.loads(fixture_path.read_text())

    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.schemas.base import FinancialsReport, MetricValue

    report = EdgarAdapter.parse_financials(raw)

    assert isinstance(report, FinancialsReport)
    assert report.ticker == "AAPL"
    assert report.cik == "0000320193"
    assert len(report.revenue) >= 1
    assert all(isinstance(m, MetricValue) for m in report.revenue)
    assert report.free_cash_flow[0].source == "calculated"
    assert report.total_debt.value > 0
    assert report.shares_outstanding.unit == "shares"


def test_smoke_llm_client_importable():
    from due_diligence.llm.client import get_client, LLMClient
    # Verify the singleton factory is importable and returns the right type
    # (does not make a real API call)
    import importlib
    import due_diligence.llm.client as mod
    importlib.reload(mod)
    client = mod.get_client()
    assert isinstance(client, LLMClient)
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_smoke.py -v
```

Expected: FAIL on import errors.

- [ ] **Step 3: Write `src/due_diligence/cli.py`**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Due Diligence — value investing analysis system")
console = Console()


@app.command("get-financials")
def get_financials_cmd(
    ticker: str = typer.Argument(..., help="US stock ticker (e.g. AAPL)"),
    save_fixture: Optional[Path] = typer.Option(
        None, "--save-fixture", help="Save raw XBRL response to a JSON fixture file"
    ),
) -> None:
    """Fetch and display key financials for a US ticker from SEC EDGAR."""
    from due_diligence.tools.edgar import EdgarAdapter

    console.print(f"[bold]Fetching EDGAR data for {ticker.upper()}…[/bold]")

    raw = EdgarAdapter.fetch_raw(ticker)

    if save_fixture:
        save_fixture.write_text(json.dumps(raw, indent=2))
        console.print(f"[green]Fixture saved to {save_fixture}[/green]")

    report = EdgarAdapter.parse_financials(raw)

    table = Table(title=f"{report.ticker} — {report.source_filing}", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value (USD B)", justify="right")
    table.add_column("Period")
    table.add_column("Source")

    def _b(v: float) -> str:
        return f"{v / 1e9:.1f}"

    if report.revenue:
        r = report.revenue[0]
        table.add_row("Revenue", _b(r.value), r.period, r.source)
    if report.gross_profit:
        g = report.gross_profit[0]
        table.add_row("Gross Profit", _b(g.value), g.period, g.source)
    if report.operating_income:
        o = report.operating_income[0]
        table.add_row("Operating Income", _b(o.value), o.period, o.source)
    if report.net_income:
        n = report.net_income[0]
        table.add_row("Net Income", _b(n.value), n.period, n.source)
    if report.free_cash_flow:
        f = report.free_cash_flow[0]
        table.add_row("Free Cash Flow", _b(f.value), f.period, f.source)
    table.add_row("Total Debt", _b(report.total_debt.value), report.total_debt.period, report.total_debt.source)
    table.add_row("Cash + STI", _b(report.cash_and_equivalents.value), report.cash_and_equivalents.period, "XBRL")
    table.add_row("CIK", report.cik, "—", "—")

    console.print(table)
```

- [ ] **Step 4: Run smoke tests to verify they pass**

```bash
uv run pytest tests/test_smoke.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -q
```

Expected: all tests across all test files PASS, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/due_diligence/cli.py tests/test_smoke.py
git commit -m "feat: CLI get-financials command and passing smoke test"
```

---

## Task 8: Definition of Done verification

This task has no new code. It verifies the Phase 0 DoD from README §6.

- [ ] **Step 1: `pytest -q` is green**

```bash
uv run pytest -q
```

Expected output (example):
```
..................
18 passed in X.XXs
```

Zero failures, zero errors.

- [ ] **Step 2: Live CLI command works against EDGAR**

This is the only step in Phase 0 that makes a real network call. Requires `.env` with valid `ANTHROPIC_API_KEY` and `SEC_USER_AGENT`.

```bash
uv run due-diligence get-financials AAPL
```

Expected: a `rich` table printed to terminal with AAPL revenue, gross profit, operating income, net income, FCF, debt, cash, CIK — all values sourced from XBRL (source column shows "XBRL" or "calculated"). No exceptions.

- [ ] **Step 3: Audit 3 numbers against the 10-K**

Open [AAPL 10-K FY2024](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=10-K&dateb=&owner=include&count=1) and verify that:
- Revenue printed ≈ $391.0B ✓
- Net Income ≈ $93.7B ✓
- Total Debt ≈ $96.7B ✓

If any value is wrong, the bug is in `EdgarAdapter.fetch_raw()` XBRL concept mapping, not in `parse_financials()` (which was tested with fixture). Fix the concept name in `fetch_raw()`, re-test.

- [ ] **Step 4: Save fixture for future tests (optional but recommended)**

```bash
uv run due-diligence get-financials AAPL --save-fixture tests/fixtures/aapl_xbrl_raw_live.json
```

Compare values to `aapl_xbrl_raw.json` (hardcoded). If they match closely, the hardcoded fixture is valid. If they differ significantly, update `aapl_xbrl_raw.json` and re-run tests.

- [ ] **Step 5: Mark MCE items as resolved in `docs/mce-evolution.md`**

Move MCE-01, MCE-02, MCE-04, MCE-05 to the "Resueltos" section with today's date.

- [ ] **Step 6: Final commit**

```bash
git add docs/mce-evolution.md
git commit -m "docs: mark MCE-01 02 04 05 resolved after Phase 0 scaffolding"
```

---

## Self-review

**Spec coverage check:**

| README §6 Phase 0 requirement | Task |
|---|---|
| `pyproject.toml` con `uv`, `.env.example`, `.gitignore` | Task 1 |
| `llm_client.py` thin wrapper Claude + OpenAI fallback | Task 5 |
| Pydantic models: Citation, Claim, MetricValue, FinancialsReport | Task 4 |
| Tool: `edgar_get_financials(ticker) -> FinancialsReport` | Task 6 |
| `tests/test_smoke.py` ejecutar tool para AAPL + validar shape | Task 7 |
| DoD: `pytest -q` verde | Task 8 step 1 |
| DoD: CLI imprime ratios correctos | Task 8 step 2 |
| MCE gate — CLAUDE.md files triggered by new directories | Task 2 |

**Placeholder scan:** None found. All steps contain complete code or exact shell commands.

**Type consistency check:**
- `MetricValue` defined in Task 4, used in Task 6 (`parse_financials`) and Task 7 (smoke test) — consistent.
- `FinancialsReport` defined in Task 4, returned by `EdgarAdapter.parse_financials` in Task 6 — consistent.
- `EdgarAdapter.parse_financials(raw: dict)` defined in Task 6, called in Tasks 7 and 8 — consistent.
- `get_client()` returns `LLMClient` singleton in Task 5, imported in smoke test Task 7 — consistent.
