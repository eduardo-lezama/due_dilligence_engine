# DCF Realism + Convergence Signaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat 10-year DCF with a 3-stage Damodaran fade model, add EPV as a zero-growth anchor (Greenwald), a simple WACC advisory, and a 4-method convergence table that the Value Expert uses to improve verdict conviction.

**Architecture:** Bundle 1 (ADR-029) — 3-stage DCF + EPV; Bundle 2 (ADR-030) — convergence signaling. Pure functions first, then agent integration, then CLI/archiver/Value Expert context.

**Tech Stack:** Pure Python arithmetic, Pydantic v2, LangGraph state, Typer CLI.

---

## Pre-conditions

- `uv run pytest -q` passes (327 green) before touching anything.
- Working branch: `main` (this project has no worktree isolation).
- Key files: `src/due_diligence/agents/valuation.py`, `src/due_diligence/schemas/reports.py`, `src/due_diligence/cli.py`, `src/due_diligence/agents/archiver.py`, `src/due_diligence/agents/value_expert.py`.

---

## Task 1 — Schema changes

**Files:**
- Modify: `src/due_diligence/schemas/reports.py:142` (`ValuationInput`)
- Modify: `src/due_diligence/schemas/reports.py:151` (`ValuationReport`)

- [ ] **Step 1: Add 2 fields to ValuationInput**

```python
class ValuationInput(CheckpointInput):
    wacc_pct: float = 9.0
    terminal_growth_pct: float = 2.5
    projection_years: int = 10
    stage1_years: Optional[int] = None   # None → auto: projection_years // 2
    fade_years: Optional[int] = None     # None → auto: projection_years - stage1_years
    revenue_growth_yr1_pct: Optional[float] = None
    margin_assumption_pct: Optional[float] = None
```

- [ ] **Step 2: Add 8 fields to ValuationReport**

```python
# EPV anchor (Greenwald)
epv_enterprise: Optional[MetricValue] = None      # NOPAT_median / WACC (enterprise value)
epv_per_share: Optional[MetricValue] = None       # (EPV_enterprise - net_debt) / shares
epv_upside_pct: Optional[float] = None            # (epv_per_share - market_price) / market_price
epv_source: Optional[str] = None                  # "ebit_median_5y"|"ebit_median_3y"|"ebit_latest"

# WACC advisory
suggested_wacc_pct: Optional[float] = None
suggested_wacc_breakdown: Optional[str] = None    # "Rf=4.2% + β=1.0×ERP=5.0% + size_premium=0.5%"

# Convergence table
valuation_methods_summary: list[dict] = Field(default_factory=list)

# 3-stage split used (for archiver + LLM context)
stage1_years_used: Optional[int] = None
fade_years_used: Optional[int] = None
```

- [ ] **Step 3: Run tests — expect all to pass (schema changes are additive)**

```
uv run pytest -q
```
Expected: 327 passed (no failures — new Optional fields don't break existing tests).

- [ ] **Commit**

```
git add src/due_diligence/schemas/reports.py
git commit -m "feat(valuation): schema fields for 3-stage DCF, EPV, WACC advisory, convergence"
```

---

## Task 2 — Pure functions (valuation.py)

**Files:**
- Modify: `src/due_diligence/agents/valuation.py` (replace `_two_stage_dcf`, update `_reverse_dcf`, add 7 new pure functions)
- Create: `tests/test_valuation_epv.py`
- Create: `tests/test_valuation_convergence.py`
- Create: `tests/test_valuation_wacc_advisory.py`

### 2a — Replace `_two_stage_dcf` with `_three_stage_dcf`

- [ ] **Step 1: Write failing test for 3-stage DCF**

```python
# tests/test_valuation.py — add to existing file
def test_three_stage_dcf_with_fade():
    """Fade reduces value vs flat growth."""
    from due_diligence.agents.valuation import _three_stage_dcf
    # Flat g=10% 10 years
    flat = _three_stage_dcf(100_000_000, g1=0.10, terminal_growth=0.025,
                             wacc=0.09, stage1_years=10, fade_years=0)
    # 3-stage: 5y at 10% + 5y fade to 2.5%
    staged = _three_stage_dcf(100_000_000, g1=0.10, terminal_growth=0.025,
                               wacc=0.09, stage1_years=5, fade_years=5)
    assert staged < flat  # fade must produce lower value
    assert staged > 0

def test_three_stage_dcf_fade_zero_equivalence():
    """fade_years=0 must equal old 2-stage behavior."""
    from due_diligence.agents.valuation import _three_stage_dcf, _two_stage_dcf
    v1 = _two_stage_dcf(100_000_000, 0.10, 0.025, 0.09, 10)
    v2 = _three_stage_dcf(100_000_000, g1=0.10, terminal_growth=0.025,
                           wacc=0.09, stage1_years=10, fade_years=0)
    assert abs(v1 - v2) < 1  # essentially identical
```

- [ ] **Step 2: Run — expect NameError (function doesn't exist yet)**

- [ ] **Step 3: Implement `_three_stage_dcf` (replaces `_two_stage_dcf` at L69)**

```python
def _three_stage_dcf(
    base_fcf: float,
    g1: float,
    terminal_growth: float,
    wacc: float,
    stage1_years: int,
    fade_years: int = 0,
) -> float:
    """
    DCF en tres etapas — Damodaran fade method (ADR-029).

    Stage 1 (años 1..stage1_years): crecimiento constante a g1.
    Stage 2 (años stage1_years+1..stage1_years+fade_years): fade lineal g1→terminal_growth.
    Stage 3: perpetuidad a terminal_growth.

    fade_years=0 degrada al 2-stage clásico (compatibilidad retroactiva).
    """
    if wacc <= terminal_growth:
        terminal_growth = wacc - 0.005

    pv = 0.0
    fcf = base_fcf

    # Stage 1
    for t in range(1, stage1_years + 1):
        fcf *= 1 + g1
        pv += fcf / (1 + wacc) ** t

    # Stage 2: fade lineal
    for fade_t in range(1, fade_years + 1):
        g_t = g1 - (g1 - terminal_growth) * fade_t / fade_years if fade_years > 0 else g1
        fcf *= 1 + g_t
        t = stage1_years + fade_t
        pv += fcf / (1 + wacc) ** t

    # Stage 3: perpetuidad
    total_explicit = stage1_years + fade_years
    terminal_fcf = fcf * (1 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv += terminal_value / (1 + wacc) ** total_explicit

    return pv


def _two_stage_dcf(
    base_fcf: float,
    near_term_growth: float,
    terminal_growth: float,
    wacc: float,
    years: int,
) -> float:
    """Wrapper de compatibilidad — delega a _three_stage_dcf con fade_years=0."""
    return _three_stage_dcf(base_fcf, near_term_growth, terminal_growth, wacc,
                             stage1_years=years, fade_years=0)
```

- [ ] **Step 4: Run tests — expect passing**

```
uv run pytest tests/test_valuation.py -q
```

### 2b — `_resolve_stages` helper

- [ ] **Step 5: Add `_resolve_stages`**

```python
def _resolve_stages(
    projection_years: int,
    stage1_years: Optional[int],
    fade_years: Optional[int],
) -> tuple[int, int]:
    """
    Calcula stage1_years y fade_years con defaults inteligentes.

    Si el usuario no especifica nada: split 50/50.
    Si especifica solo stage1: fade = projection_years - stage1.
    Si especifica ambos: usarlos directamente (ignore projection_years).
    Si la suma excede projection_years: recortar fade.
    """
    if stage1_years is not None and fade_years is not None:
        return stage1_years, fade_years
    if stage1_years is not None:
        return stage1_years, max(0, projection_years - stage1_years)
    # Auto: split 50/50, con stage1 >= 1
    s1 = max(1, projection_years // 2)
    return s1, projection_years - s1
```

### 2c — EPV pure functions

- [ ] **Step 6: Write failing EPV tests**

```python
# tests/test_valuation_epv.py
import pytest
from due_diligence.agents.valuation import _ebit_median, _compute_epv
from due_diligence.schemas.base import MetricValue

def make_mv(value: float, period: str = "FY2024") -> MetricValue:
    return MetricValue(value=value, unit="USD", period=period, source="test")

def test_ebit_median_5y():
    series = [make_mv(v, f"FY{2020+i}") for i, v in enumerate([100, 120, 110, 130, 115])]
    med, source = _ebit_median(series)
    assert med == pytest.approx(115)
    assert "5y" in source or "median" in source

def test_ebit_median_falls_back_to_latest():
    series = [make_mv(100)]
    med, source = _ebit_median(series)
    assert med == 100
    assert "latest" in source

def test_compute_epv_enterprise():
    series = [make_mv(v) for v in [200e6, 180e6, 190e6, 210e6, 195e6]]
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=series,
        net_income_latest=150e6,
        wacc=0.09,
        net_debt=0,
        shares=10e6,
        market_cap=1.5e9,
    )
    assert epv_ent is not None
    # NOPAT ~= 190M*(1-0.21)~=150M / 0.09 = ~1.67B
    assert 1.5e9 < epv_ent.value < 2e9

def test_compute_epv_per_share_subtracts_net_debt():
    series = [make_mv(180e6)]
    epv_ent, epv_ps, upside, source = _compute_epv(
        ebit_series=series,
        net_income_latest=None,
        wacc=0.09,
        net_debt=200e6,      # 200M deuda neta
        shares=10e6,
        market_cap=1e9,
    )
    assert epv_ent is not None
    # epv_enterprise = 180M*(1-0.25)/0.09 = 135M/0.09 = 1.5B
    # epv_equity = 1.5B - 200M = 1.3B
    # epv_per_share = 1.3B / 10M = $130
    assert epv_ps is not None
    assert abs(epv_ps.value - 130) < 5  # tolerance for tax rounding
```

- [ ] **Step 7: Implement `_ebit_median` and `_compute_epv`**

```python
def _ebit_median(
    ebit_series: list[MetricValue],
    min_periods: int = 3,
) -> tuple[Optional[float], str]:
    """
    Mediana del EBIT en últimos periodos (Greenwald: valor normalizado).

    Returns (median_value, source_label). source: "ebit_median_5y",
    "ebit_median_3y", "ebit_latest", or (None, "unavailable").
    """
    if not ebit_series:
        return None, "unavailable"
    values = [mv.value for mv in ebit_series if mv.value is not None and mv.value > 0]
    if not values:
        return None, "unavailable"
    if len(values) >= 5:
        subset = values[:5]
        label = "ebit_median_5y"
    elif len(values) >= 3:
        subset = values[:min_periods]
        label = f"ebit_median_{len(values)}y"
    else:
        subset = values[:1]
        label = "ebit_latest"
    subset_sorted = sorted(subset)
    n = len(subset_sorted)
    if n % 2 == 1:
        med = subset_sorted[n // 2]
    else:
        med = (subset_sorted[n // 2 - 1] + subset_sorted[n // 2]) / 2
    return med, label


def _compute_epv(
    ebit_series: list[MetricValue],
    net_income_latest: Optional[float],
    wacc: float,
    net_debt: float,
    shares: Optional[float],
    market_cap: Optional[float],
) -> tuple[Optional[MetricValue], Optional[MetricValue], Optional[float], Optional[str]]:
    """
    Earnings Power Value (Greenwald).

    EPV_enterprise = NOPAT_median / WACC
    EPV_equity = EPV_enterprise - net_debt
    EPV_per_share = EPV_equity / shares

    Tax rate derivada de net_income/EBIT si disponible, else 25%.
    Usa mediana EBIT (normalizado) para evitar picos cíclicos.

    Returns: (epv_enterprise_mv, epv_per_share_mv, epv_upside_pct, source_label).
    """
    ebit_med, source = _ebit_median(ebit_series)
    if ebit_med is None or wacc <= 0:
        return None, None, None, None

    # Derivar tax rate
    ebit_latest = ebit_series[0].value if ebit_series else None
    if net_income_latest is not None and net_income_latest > 0 and ebit_latest and ebit_latest > net_income_latest:
        derived_tax = 1 - (net_income_latest / ebit_latest)
        tax_rate = max(0.10, min(derived_tax, 0.40))
    else:
        tax_rate = 0.25

    nopat = ebit_med * (1 - tax_rate)
    epv_enterprise = nopat / wacc

    period = ebit_series[0].period if ebit_series else "N/A"

    epv_ent_mv = MetricValue(
        value=round(epv_enterprise),
        unit="USD",
        period=period,
        source=f"EPV ({source})",
    )

    epv_ps_mv: Optional[MetricValue] = None
    epv_upside: Optional[float] = None

    if shares is not None and shares > 0:
        epv_equity = epv_enterprise - net_debt
        epv_per_share = epv_equity / shares
        epv_ps_mv = MetricValue(
            value=round(epv_per_share, 2),
            unit="USD/share",
            period=period,
            source=f"EPV ({source})",
        )

        if market_cap is not None and market_cap > 0:
            # upside como % vs market cap (enterprise-level comparison)
            epv_upside = round((epv_enterprise - market_cap) / market_cap * 100, 1)

    return epv_ent_mv, epv_ps_mv, epv_upside, source
```

- [ ] **Step 8: Run EPV tests**

```
uv run pytest tests/test_valuation_epv.py -v
```
Expected: all pass.

### 2d — WACC advisory

- [ ] **Step 9: Write failing WACC tests**

```python
# tests/test_valuation_wacc_advisory.py
from due_diligence.agents.valuation import _compute_suggested_wacc

def test_large_cap_no_size_premium():
    wacc, breakdown = _compute_suggested_wacc(market_cap=50e9)
    assert abs(wacc - 9.2) < 0.1   # Rf=4.2 + 1.0*5.0 + 0.0
    assert "0.0%" in breakdown or "large cap" in breakdown.lower()

def test_small_cap_adds_premium():
    wacc, breakdown = _compute_suggested_wacc(market_cap=500e6)
    assert abs(wacc - 10.7) < 0.1  # Rf=4.2 + 5.0 + 1.5
    assert "1.5%" in breakdown

def test_no_market_cap_defaults_to_mid():
    wacc, breakdown = _compute_suggested_wacc(market_cap=None)
    assert wacc > 9.0
```

- [ ] **Step 10: Implement `_compute_suggested_wacc`**

```python
# Constants — update Rf manually as rates change (as of 2026-05)
_RF_PCT = 4.2       # US 10y Treasury yield
_ERP_PCT = 5.0      # Damodaran historical ERP (US)
_BETA = 1.0         # Simplification — market beta, no company-specific calc

def _compute_suggested_wacc(
    market_cap: Optional[float],
    rf_pct: float = _RF_PCT,
    erp_pct: float = _ERP_PCT,
    beta: float = _BETA,
) -> tuple[float, str]:
    """
    WACC advisory por CAPM simplificado (ADR-029).

    size_premium por market cap bucket (Damodaran tablas 2024):
    >$10B (large): 0%   |   $2-10B (mid): 0.5%
    $300M-$2B (small): 1.5%   |   <$300M (micro): 3%

    β=1.0 es simplificación — usar override manual si se tiene β real.
    No incluye country risk premium (US-only scope Fase 1-2).
    """
    if market_cap is None or market_cap >= 10e9:
        size_label, size_prem = "large cap", 0.0
    elif market_cap >= 2e9:
        size_label, size_prem = "mid cap", 0.5
    elif market_cap >= 300e6:
        size_label, size_prem = "small cap", 1.5
    else:
        size_label, size_prem = "micro cap", 3.0

    suggested = rf_pct + beta * erp_pct + size_prem
    breakdown = (
        f"Rf={rf_pct}% + β={beta}×ERP={erp_pct}% + size_premium={size_prem}% ({size_label})"
    )
    return round(suggested, 1), breakdown
```

- [ ] **Step 11: Run WACC tests**

```
uv run pytest tests/test_valuation_wacc_advisory.py -v
```

### 2e — Convergence classifiers

- [ ] **Step 12: Write failing convergence tests**

```python
# tests/test_valuation_convergence.py
from due_diligence.agents.valuation import (
    _classify_dcf_upside, _classify_epv_upside,
    _classify_reverse_vs_historical, _classify_ev_ebit_vs_peers,
    _build_methods_summary,
)

def test_dcf_cheap():
    assert _classify_dcf_upside(25.0) == "cheap"

def test_dcf_fair():
    assert _classify_dcf_upside(10.0) == "fair"

def test_dcf_expensive():
    assert _classify_dcf_upside(-25.0) == "expensive"

def test_dcf_none():
    assert _classify_dcf_upside(None) == "n/a"

def test_epv_cheap():
    assert _classify_epv_upside(15.0) == "cheap"

def test_epv_fair():
    assert _classify_epv_upside(5.0) == "fair"  # paying some for growth, EPV below market

def test_epv_expensive():
    assert _classify_epv_upside(-20.0) == "expensive"

def test_reverse_cheap():
    # implied 3%, historical 8% → spread -5pp → cheap
    assert _classify_reverse_vs_historical(0.03, 0.08) == "cheap"

def test_reverse_expensive():
    # implied 15%, historical 8% → spread +7pp → expensive (market very optimistic)
    assert _classify_reverse_vs_historical(0.15, 0.08) == "expensive"

def test_ev_ebit_cheap():
    assert _classify_ev_ebit_vs_peers(8.0, 15.0) == "cheap"

def test_ev_ebit_expensive():
    assert _classify_ev_ebit_vs_peers(25.0, 15.0) == "expensive"

def test_build_methods_summary_returns_4_rows():
    from due_diligence.schemas.base import MetricValue
    mv = MetricValue(value=100.0, unit="USD/share", period="FY2024", source="test")
    rows = _build_methods_summary(
        dcf_per_share=mv, dcf_upside_pct=30.0,
        epv_per_share=mv, epv_upside_pct=15.0,
        reverse_growth=0.04, historical_cagr=0.10,
        ev_ebit=MetricValue(value=12.0, unit="x", period="FY2024", source="test"),
        peer_ev_ebit_median=18.0,
    )
    assert len(rows) == 4
    assert all("method" in r and "classification" in r for r in rows)
```

- [ ] **Step 13: Implement convergence functions**

```python
def _classify_dcf_upside(upside_pct: Optional[float]) -> str:
    if upside_pct is None:
        return "n/a"
    if upside_pct > 20:
        return "cheap"
    if upside_pct < -20:
        return "expensive"
    return "fair"


def _classify_epv_upside(upside_pct: Optional[float]) -> str:
    """EPV vs market cap. >10 = cheap (market below no-growth value), 0-10 = fair, <0 = expensive."""
    if upside_pct is None:
        return "n/a"
    if upside_pct > 10:
        return "cheap"
    if upside_pct < 0:
        return "expensive"
    return "fair"


def _classify_reverse_vs_historical(
    implied_growth: Optional[float],
    historical_cagr: Optional[float],
    threshold: float = 0.03,
) -> str:
    """Implied < hist-3pp = cheap (market pessimistic). Implied > hist+3pp = expensive."""
    if implied_growth is None or historical_cagr is None:
        return "n/a"
    spread = implied_growth - historical_cagr
    if spread < -threshold:
        return "cheap"
    if spread > threshold:
        return "expensive"
    return "fair"


def _classify_ev_ebit_vs_peers(
    subject_ev_ebit: Optional[float],
    peer_ev_ebit_median: Optional[float],
) -> str:
    """<0.7× peer median = cheap. >1.3× = expensive."""
    if subject_ev_ebit is None or peer_ev_ebit_median is None or peer_ev_ebit_median <= 0:
        return "n/a"
    ratio = subject_ev_ebit / peer_ev_ebit_median
    if ratio < 0.70:
        return "cheap"
    if ratio > 1.30:
        return "expensive"
    return "fair"


def _build_methods_summary(
    dcf_per_share: Optional[MetricValue],
    dcf_upside_pct: Optional[float],
    epv_per_share: Optional[MetricValue],
    epv_upside_pct: Optional[float],
    reverse_growth: Optional[float],
    historical_cagr: Optional[float],
    ev_ebit: Optional[MetricValue],
    peer_ev_ebit_median: Optional[float],
) -> list[dict]:
    """
    Tabla de 4 métodos de valoración con clasificación uniform (cheap/fair/expensive).
    Consumida por Value Expert _format_context() y Archiver.
    """
    def _fmt_pct(v): return f"{v:.1%}" if v is not None else "N/A"
    def _fmt_x(v): return f"{v:.1f}x" if v is not None else "N/A"
    def _fmt_usd(v): return f"${v:.2f}" if v is not None else "N/A"

    rows = [
        {
            "method": "DCF (3-stage Damodaran fade)",
            "per_share": _fmt_usd(dcf_per_share.value if dcf_per_share else None),
            "upside_pct": dcf_upside_pct,
            "display_upside": f"{dcf_upside_pct:+.1f}%" if dcf_upside_pct is not None else "N/A",
            "classification": _classify_dcf_upside(dcf_upside_pct),
        },
        {
            "method": "EPV (Greenwald — zero growth)",
            "per_share": _fmt_usd(epv_per_share.value if epv_per_share else None),
            "upside_pct": epv_upside_pct,
            "display_upside": f"{epv_upside_pct:+.1f}%" if epv_upside_pct is not None else "N/A",
            "classification": _classify_epv_upside(epv_upside_pct),
        },
        {
            "method": "Reverse DCF (market-implied growth)",
            "per_share": "N/A",
            "upside_pct": None,
            "display_upside": f"implied {_fmt_pct(reverse_growth)} vs historical {_fmt_pct(historical_cagr)}",
            "classification": _classify_reverse_vs_historical(reverse_growth, historical_cagr),
        },
        {
            "method": "EV/EBIT vs peer median",
            "per_share": "N/A",
            "upside_pct": None,
            "display_upside": f"{_fmt_x(ev_ebit.value if ev_ebit else None)} vs peer median {_fmt_x(peer_ev_ebit_median)}",
            "classification": _classify_ev_ebit_vs_peers(
                ev_ebit.value if ev_ebit else None,
                peer_ev_ebit_median,
            ),
        },
    ]
    return rows
```

- [ ] **Step 14: Run convergence tests**

```
uv run pytest tests/test_valuation_convergence.py tests/test_valuation_epv.py tests/test_valuation_wacc_advisory.py -v
```
Expected: all pass.

- [ ] **Commit**

```
git add src/due_diligence/agents/valuation.py tests/test_valuation_epv.py tests/test_valuation_convergence.py tests/test_valuation_wacc_advisory.py
git commit -m "feat(valuation): 3-stage DCF, EPV, WACC advisory, convergence pure functions"
```

---

## Task 3 — Integrate into `valuation_agent()`

**Files:**
- Modify: `src/due_diligence/agents/valuation.py:342` (`valuation_agent`)

- [ ] **Step 1: Resolve stages early (after building `assumptions`)**

After L371 (`years = assumptions.projection_years`), insert:
```python
stage1_years, fade_years = _resolve_stages(
    years, assumptions.stage1_years, assumptions.fade_years
)
```

- [ ] **Step 2: Replace `_two_stage_dcf` call (L525) with `_three_stage_dcf`**

```python
# Before:
equity_value = _two_stage_dcf(dcf_base, near_term_growth, terminal_growth, wacc, years)
# After:
equity_value = _three_stage_dcf(dcf_base, near_term_growth, terminal_growth, wacc,
                                  stage1_years=stage1_years, fade_years=fade_years)
```

- [ ] **Step 3: Update `_reverse_dcf` call (L548) to use new stage parameters**

```python
# _reverse_dcf needs to call _three_stage_dcf internally — update _reverse_dcf signature**
```

Update `_reverse_dcf` (L101) to accept and forward `stage1_years` and `fade_years`:
```python
def _reverse_dcf(
    base_fcf, target_value, terminal_growth, wacc, years,
    stage1_years: Optional[int] = None, fade_years: Optional[int] = None,
) -> Optional[float]:
    s1 = stage1_years if stage1_years is not None else years
    fd = fade_years if fade_years is not None else 0
    ...
    val = _three_stage_dcf(base_fcf, mid, terminal_growth, wacc, stage1_years=s1, fade_years=fd)
```

And in `valuation_agent()`:
```python
reverse_growth = _reverse_dcf(dcf_base, market_cap, terminal_growth, wacc, years,
                               stage1_years=stage1_years, fade_years=fade_years)
```

- [ ] **Step 4: Add EPV calculation (after DCF block, before multiples)**

```python
# --- EPV (Greenwald) ---
net_debt = (total_debt or 0.0) - (cash or 0.0)
epv_ent_mv, epv_ps_mv, epv_upside_pct, epv_source = _compute_epv(
    ebit_series=financials.operating_income if financials else [],
    net_income_latest=net_income,
    wacc=wacc,
    net_debt=net_debt,
    shares=shares,
    market_cap=market_cap,
)
```

- [ ] **Step 5: Add WACC advisory**

```python
# --- WACC advisory ---
suggested_wacc, wacc_breakdown = _compute_suggested_wacc(market_cap=market_cap)
```

- [ ] **Step 6: Compute peer_ev_ebit_median for convergence table**

```python
peer_ev_ebit_values = [
    pv.ev_ebit for pv in peer_valuations_raw
    if pv.ev_ebit is not None and not pv.no_market_data
]
peer_ev_ebit_median: Optional[float] = None
if peer_ev_ebit_values:
    sv = sorted(peer_ev_ebit_values)
    n = len(sv)
    peer_ev_ebit_median = sv[n // 2] if n % 2 else (sv[n//2-1]+sv[n//2])/2
```

- [ ] **Step 7: Build convergence table (after all metrics computed)**

```python
historical_cagr_for_conv = near_term_growth if near_term_growth_source != "user_override" else revenue_cagr
methods_summary = _build_methods_summary(
    dcf_per_share=dcf_per_share,
    dcf_upside_pct=dcf_upside,
    epv_per_share=epv_ps_mv,
    epv_upside_pct=epv_upside_pct,
    reverse_growth=reverse_growth,
    historical_cagr=historical_cagr_for_conv,
    ev_ebit=ev_ebit_mv,
    peer_ev_ebit_median=peer_ev_ebit_median,
)
```

- [ ] **Step 8: Add new fields to `ValuationReport` construction (L658)**

```python
report = ValuationReport(
    ...  # existing fields unchanged
    epv_enterprise=epv_ent_mv,
    epv_per_share=epv_ps_mv,
    epv_upside_pct=epv_upside_pct,
    epv_source=epv_source,
    suggested_wacc_pct=suggested_wacc,
    suggested_wacc_breakdown=wacc_breakdown,
    valuation_methods_summary=methods_summary,
    stage1_years_used=stage1_years,
    fade_years_used=fade_years,
)
```

- [ ] **Step 9: Update sensitivity table call**

The sensitivity table still varies `g1` (=`near_term_growth`). No change needed — it already calls `_two_stage_dcf` which now delegates. But the sensitivity table internally should use the 3-stage format. Update `_sensitivity_table`:

```python
def _sensitivity_table(..., stage1_years: int, fade_years: int) -> list[dict]:
    ...
    val = _three_stage_dcf(base_fcf, g, terminal_growth, w,
                            stage1_years=stage1_years, fade_years=fade_years)
```

- [ ] **Step 10: Run full test suite — expect some DCF numeric failures**

```
uv run pytest -q
```
Note which numeric tests fail. These are expected (3-stage produces lower values).

- [ ] **Step 11: Update failing tests — recalibrate expected values**

For each test that asserts a specific DCF numeric value:
- Recompute with `_three_stage_dcf(base_fcf, g1, terminal, wacc, stage1=5, fade=5)` for default splits.
- Replace hardcoded `assert dcf_value == X` with the new value (or use a range: `assert X*0.7 < dcf_value < X*0.95`).

- [ ] **Step 12: Run archetype tests**

```
uv run pytest tests/test_edgar_archetypes.py -v
```

- [ ] **Commit**

```
git add src/due_diligence/agents/valuation.py
git commit -m "feat(valuation): integrate 3-stage DCF + EPV + WACC advisory + convergence into valuation_agent"
```

---

## Task 4 — CLI changes

**Files:**
- Modify: `src/due_diligence/cli.py` (`_ask_dcf_assumptions`)

- [ ] **Step 1: Show suggested WACC before prompt**

In `_ask_dcf_assumptions()`, after computing `auto_growth`:
```python
# Show suggested WACC advisory before asking
from due_diligence.agents.valuation import _compute_suggested_wacc
sugg_wacc, wacc_breakdown = _compute_suggested_wacc(market_cap=market_cap)
console.print(f"\n[dim]Suggested WACC: {sugg_wacc:.1f}% ({wacc_breakdown})[/dim]")
console.print("[dim]Note: β=1.0 is a simplification. Override manually if you have a better estimate.[/dim]")
```

- [ ] **Step 2: Show stage split after projection_years prompt**

After user enters `projection_years`:
```python
from due_diligence.agents.valuation import _resolve_stages
s1, fd = _resolve_stages(years, None, None)
console.print(f"[dim]Auto stage split: {s1}y constant growth + {fd}y fade to terminal.[/dim]")
console.print("[dim]To override, enter 'stage1,fade' (e.g. '3,4'). Press Enter to accept.[/dim]")
stage_input = Prompt.ask("Stage split", default="auto")
if stage_input != "auto":
    parts = stage_input.split(",")
    stage1_override = int(parts[0].strip())
    fade_override = int(parts[1].strip()) if len(parts) > 1 else years - stage1_override
else:
    stage1_override, fade_override = None, None
```

Pass `stage1_years=stage1_override, fade_years=fade_override` into `ValuationInput`.

- [ ] **Step 3: Run CLI smoke test** (no real API — use checkpoint mock if available)

```
uv run pytest tests/test_cli.py -q
```

- [ ] **Commit**

```
git add src/due_diligence/cli.py
git commit -m "feat(cli): show suggested WACC + 3-stage split preview at checkpoint 2"
```

---

## Task 5 — Archiver

**Files:**
- Modify: `src/due_diligence/agents/archiver.py`

- [ ] **Step 1: Update DCF footnote to include stage split**

Find the DCF footnote line in `archiver.py`:
```
*DCF assumptions: WACC=X%, near-term growth=Y% (source), terminal=Z%, N years*
```
Replace with:
```
*DCF assumptions: WACC=X%, stage 1 growth=Y% ({source}) × {s1}y, fade to {terminal}% over {fd}y, terminal={terminal}%, {total}y explicit*
```

- [ ] **Step 2: Add EPV row to valuation table**

Where the DCF per share and upside are printed, add:
```markdown
| EPV (no growth) | ${epv_per_share}/sh | {epv_upside_pct}% | NOPAT median / WACC |
```

- [ ] **Step 3: Add convergence table section**

```markdown
### Valuation Methods Convergence

| Method | Per Share | Upside | Signal |
|---|---|---|---|
| DCF (3-stage) | $X | +Y% | cheap |
| EPV (zero growth) | $X | +Y% | fair |
| Reverse DCF | N/A | implied Z% vs hist H% | cheap |
| EV/EBIT vs peers | N/A | Xx vs peer Yx | expensive |

Convergence: N cheap, M fair, K expensive.
```

- [ ] **Step 4: Run archiver tests**

```
uv run pytest tests/test_archiver.py -q
```

- [ ] **Commit**

```
git add src/due_diligence/agents/archiver.py tests/test_archiver.py
git commit -m "feat(archiver): EPV anchor + convergence table in valuation section"
```

---

## Task 6 — Value Expert context and prompt

**Files:**
- Modify: `src/due_diligence/agents/value_expert.py` (`_format_context`, system prompt)

- [ ] **Step 1: Add convergence block to `_format_context()`**

After the `## DCF GROWTH ASSUMPTION` block:

```python
# Convergence block
if valuation.valuation_methods_summary:
    cheap = [r for r in valuation.valuation_methods_summary if r["classification"] == "cheap"]
    expensive = [r for r in valuation.valuation_methods_summary if r["classification"] == "expensive"]
    fair = [r for r in valuation.valuation_methods_summary if r["classification"] == "fair"]
    context_parts.append(
        f"\n## VALUATION METHODS CONVERGENCE (use in §6 — all methods available)\n"
        + "\n".join(
            f"- {r['method']}: **{r['classification'].upper()}** | {r['display_upside']}"
            for r in valuation.valuation_methods_summary
            if r["classification"] != "n/a"
        )
        + f"\n\nConvergence: {len(cheap)} cheap, {len(fair)} fair, {len(expensive)} expensive out of "
          f"{len(valuation.valuation_methods_summary)} methods."
    )
```

- [ ] **Step 2: Add EPV context line**

In the `## VALUATION` block, after DCF per share, add:
```
EPV (zero-growth floor): ${epv_per_share}/sh ({epv_upside_pct}% vs market) — source: {epv_source}
```

- [ ] **Step 3: Add WACC advisory line**

```
Suggested WACC (advisory): {suggested_wacc_pct}% ({wacc_breakdown})
User-input WACC: {wacc_pct}%
```

- [ ] **Step 4: Add Rule #21 to system prompt**

Find the numbered rules in the system prompt string. After Rule #20 (DCF growth assessment), add:

```
21. VALUATION CONVERGENCE: Read the ## VALUATION METHODS CONVERGENCE block carefully.
    - If 3 or 4 methods classify as "cheap" → conviction is high; state this explicitly in §6.
    - If methods diverge → HOLD bias by default; explain in §6 which method you weight most
      and WHY (e.g. "EPV shows fair value because the company is priced for growth it has
      not yet demonstrated").
    - EPV is the zero-growth floor: if the stock is below EPV, a BUY does NOT require
      growth assumptions to hold — state this if applicable.
    - Never ignore the convergence count in your §6 summary.
```

- [ ] **Step 5: Run value expert tests**

```
uv run pytest tests/test_value_expert.py -q
```

- [ ] **Commit**

```
git add src/due_diligence/agents/value_expert.py tests/test_value_expert.py
git commit -m "feat(value-expert): convergence block + EPV + WACC advisory in context + Rule 21"
```

---

## Task 7 — ADRs + documentation

**Files:**
- Create: `docs/adr/ADR-029-three-stage-dcf-and-epv.md`
- Create: `docs/adr/ADR-030-valuation-convergence-signaling.md`
- Modify: `docs/adr/README.md`
- Modify: `README.md`
- Modify: `DETAILED_EXPLANATION.md`

- [ ] **Step 1: Write ADR-029**

Document: Damodaran 3-stage rationale, EPV Greenwald rationale, mediana 5y decision, WACC advisory with β=1 limitation, decision to KEEP user_override (contra original proposal), `_two_stage_dcf` kept as compatibility shim.

- [ ] **Step 2: Write ADR-030**

Document: convergence signaling rationale, bucket thresholds (with justification), why LLM (not deterministic code) synthesizes verdict, relation to ADR-019 (Value Expert prompt v2), peer_ev_ebit_median computation.

- [ ] **Step 3: Update ADR README**

Add rows for ADR-029 and ADR-030.

- [ ] **Step 4: Update README.md**

In "Core design principles": add "3-stage DCF with Damodaran fade" and "EPV as zero-growth anchor". In "Status" line: update test count and ADR count.

- [ ] **Step 5: Update DETAILED_EXPLANATION.md**

Append section: "DCF realism + convergence signaling (2026-05-17)" with didactic explanation.

- [ ] **Commit**

```
git add docs/adr/ README.md DETAILED_EXPLANATION.md
git commit -m "docs(adr): ADR-029 (3-stage DCF + EPV) + ADR-030 (convergence signaling)"
```

---

## Final verification

- [ ] `uv run pytest -q` — all tests pass (including archetypes)
- [ ] `uv run pytest tests/test_edgar_archetypes.py -v` — all 4 archetypes pass
- [ ] Manual check: valuation numbers make sense (EPV < DCF for growth companies, staged DCF < flat DCF)
