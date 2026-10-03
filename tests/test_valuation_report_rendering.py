"""
Tests de presentación del plano enterprise (T13, ADR-033): archiver, contexto del Value Expert y CLI.

Qué se testea:
    - El archiver muestra el bridge EV → equity con cada componente (y el memo de leases ASC 842).
    - La sección "## Data quality flags" del archiver (FinancialsReport.data_quality_flags).
    - La cabecera de la tabla de sensibilidad usa `sensitivity_unit` ("USD B (legacy)" en reports viejos)
      y las celdas None se muestran "n/m".
    - Un ValuationReport stub con `error` se muestra como valoración no disponible, no desaparece.
    - El contexto del Value Expert lleva FCFF, WACC, bridge, % terminal y "vs price" (nunca "vs market cap").
    - `_show_valuation` (CLI) muestra EV, net claims y valor por acción.

Fichero propio (y no test_archiver.py / test_value_expert.py) para no colisionar con el Bloque A,
que reescribe esos ficheros en paralelo (R2). Valores formateados verificados con fmt_usd real (R7).
Zero red: valuation_agent es tool-lane puro; los estados se construyen a mano.
"""
import pytest
from rich.console import Console

from due_diligence.agents.archiver import _render_equity_bridge, _render_markdown
from due_diligence.agents.valuation import valuation_agent
from due_diligence.agents.valuation_math import equity_bridge
from due_diligence.agents.value_expert import _format_context
from due_diligence.schemas.base import FinancialsReport, MetricValue
from due_diligence.schemas.reports import MarketReport, ValuationReport


def _mv(v, p="FY2025", u="USD"):
    return MetricValue(value=v, unit=u, period=p, source="test")


def _levered_financials() -> FinancialsReport:
    """Ejemplo A de ADR-033 (mismo que tests/test_valuation.py)."""
    return FinancialsReport(
        ticker="LEV", cik="0000000001", as_of_date="2025-12-31", source_filing="10-K FY2025",
        revenue=[_mv(2_000e6)], gross_profit=[_mv(800e6)], operating_income=[_mv(300e6)],
        net_income=[_mv(195e6)], operating_cash_flow=[_mv(260e6)], capex=[_mv(60e6)],
        free_cash_flow=[_mv(200e6)], interest_expense=[_mv(40e6)], sbc=[_mv(20e6)],
        total_assets=_mv(3_000e6), total_debt=_mv(500e6), cash_and_equivalents=_mv(50e6),
        shares_outstanding=_mv(10e6, u="shares"), operating_lease_liabilities=_mv(120e6),
    )


def _example_a_state() -> dict:
    return {
        "ticker": "LEV", "financials": _levered_financials(),
        "market": MarketReport(ticker="LEV", stub=False, price_current=_mv(150.0, "2026-10-01"),
                               market_cap=_mv(1_500e6, "2026-10-01")),
        "checkpoint2_user_input": {"wacc_pct": 9.0, "terminal_growth_pct": 0.0, "projection_years": 10,
                                   "revenue_growth_yr1_pct": 0.0, "valuation_date": "2025-12-31"},
    }


@pytest.fixture
def state_with_valuation_example_a() -> dict:
    st = _example_a_state()
    st["valuation"] = valuation_agent(st)["valuation"]
    return st


@pytest.fixture
def state_with_old_valuation_report() -> dict:
    """Report anterior a ADR-033: sensibilidad en USD B, sin sensitivity_unit."""
    return {"ticker": "OLD", "valuation": ValuationReport(
        ticker="OLD", stub=False,
        sensitivity_table=[{"near_term_growth_pct": 10.0, "values": {"wacc_7.0pct": 3.1, "wacc_9.0pct": 2.4}}],
    )}


def _bridge_example_a():
    # EV 2 403.735M (DCF con reinversión terminal), deuda 500M, caja 50M, leases operativos 120M (memo)
    return equity_bridge(2_403.735e6, total_debt=500e6, cash=50e6, lease_liabilities=120e6)


# --- archiver ---

def test_archiver_renders_equity_bridge():
    md = "\n".join(_render_equity_bridge(_bridge_example_a(), share_count=10e6, per_share=195.37))
    assert "### Enterprise → Equity Bridge" in md
    assert "| Enterprise value (DCF, FCFF) | $2.4B |" in md          # fmt_usd(2.403e9) → "$2.4B" (verificado)
    assert "| (−) Total debt | $500M |" in md and "| (+) Cash | $50M |" in md   # fmt_usd(500e6) → "$500M"
    assert "Operating lease liabilities (memo, not deducted — ASC 842) | $120M" in md
    assert "| = Equity value | $2.0B |" in md and "$195.37" in md   # 1 953.7M → "$2.0B"


def test_archiver_renders_data_quality_flags():
    state = {"ticker": "TST",
             "financials": _levered_financials().model_copy(
                 update={"data_quality_flags": ["DebtCurrent < LongTermDebtCurrent (FY2025)"]})}
    md = _render_markdown(state)
    assert "## Data quality flags" in md and "DebtCurrent < LongTermDebtCurrent" in md


def test_archiver_sensitivity_header_unit_legacy(state_with_old_valuation_report):
    md = _render_markdown(state_with_old_valuation_report)
    assert "USD B (legacy)" in md


def test_archiver_full_valuation_section_example_a(state_with_valuation_example_a):
    md = _render_markdown(state_with_valuation_example_a)
    assert "### Enterprise → Equity Bridge" in md and "$195.37" in md
    assert "DCF upside vs price" in md and "vs market cap" not in md
    assert "DCF equity value (after bridge)" in md and "EV/FCFF" in md
    assert "USD/share" in md                                      # cabecera de sensibilidad
    assert "Terminal value share" in md and "WACC" in md


def test_archiver_sensitivity_none_cell_is_nm():
    st = {"ticker": "X", "valuation": ValuationReport(
        ticker="X", stub=False, sensitivity_unit="USD/share",
        sensitivity_table=[{"near_term_growth_pct": 5.0, "values": {"wacc_2.5pct": None, "wacc_4.5pct": 120.5}}])}
    md = _render_markdown(st)
    assert "| 5.0% | n/m | 120.5 |" in md


def test_archiver_shows_valuation_error_instead_of_hiding_it():
    st = {"ticker": "X", "valuation": ValuationReport(
        ticker="X", stub=True, error="ValuationInputError: WACC (3.00%) must exceed terminal growth")}
    md = _render_markdown(st)
    assert "Valuation not computed" in md and "ValuationInputError" in md


# --- Value Expert context ---

def test_value_expert_context_has_fcff_wacc_bridge_tv(state_with_valuation_example_a):
    ctx = _format_context(state_with_valuation_example_a)
    for s in ("FCFF base", "WACC breakdown", "Equity bridge", "Terminal value share", "vs price"):
        assert s in ctx
    assert "vs market cap" not in ctx
    assert "Operating lease liabilities (memo, not deducted" in ctx
    assert "EPV growth premium" in ctx


def test_value_expert_context_flags_terminal_value_dependence(state_with_valuation_example_a):
    st = dict(state_with_valuation_example_a)
    st["valuation"] = st["valuation"].model_copy(update={"terminal_value_share": 0.82})
    assert "§6 must flag terminal-value dependence" in _format_context(st)


def test_value_expert_context_data_quality_block():
    st = {"ticker": "TST", "financials": _levered_financials().model_copy(
        update={"data_quality_flags": ["balance identity off by 3% (FY2025)"]})}
    ctx = _format_context(st)
    assert "## DATA QUALITY FLAGS (deterministic)" in ctx and "balance identity off by 3%" in ctx


def test_value_expert_context_shows_valuation_error():
    st = {"ticker": "X", "valuation": ValuationReport(ticker="X", stub=True, error="ValuationInputError: bad")}
    assert "VALUATION UNAVAILABLE" in _format_context(st)


# --- CLI ---

def test_cli_show_valuation_rows(mocker, state_with_valuation_example_a):
    from due_diligence import cli
    rec = Console(record=True, width=200)
    mocker.patch.object(cli, "console", rec)
    cli._show_valuation(state_with_valuation_example_a)
    out = rec.export_text()
    assert "Enterprise value (DCF)" in out and "$2.4B" in out
    assert "Net claims" in out and "$450M" in out
    assert "Per share" in out and "$195.37" in out
    assert "EV/FCFF" in out
