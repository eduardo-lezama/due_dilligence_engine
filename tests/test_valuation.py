"""
Tests para valuation_agent y las funciones de cálculo DCF.

Qué se testea:
    - _two_stage_dcf(): aritmética correcta en casos base y edge cases.
    - _reverse_dcf(): convergencia y coherencia con _two_stage_dcf.
    - _sensitivity_table(): forma y claves correctas.
    - valuation_agent(): nodo LangGraph completo con estado real (fixture AAPL FY2025).
    - Degraded mode: agent corre sin market data o sin financials.
    - checkpoint2_node: lanza RuntimeError fuera del contexto del grafo.

Estrategia de fixtures:
    Para el estado del agente usamos el fixture AAPL FY2025 ya cacheado —
    parse_financials() construye un FinancialsReport real con FCF y ROIC calculados.
    Para MarketReport construimos un stub simple sin tocar Polygon.

Zero llamadas reales a APIs: el valuation_agent es tool-lane (sin LLM).
"""

import json
import pytest
from pathlib import Path

from due_diligence.agents.valuation import (
    valuation_agent,
    _two_stage_dcf,
    _reverse_dcf,
    _sensitivity_table,
    _fcf_cagr,
)
from due_diligence.schemas.reports import ValuationReport, MarketReport
from due_diligence.schemas.base import MetricValue

# Fixture real AAPL FY2025
_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())


def _build_financials():
    from due_diligence.tools.edgar import EdgarAdapter
    return EdgarAdapter.parse_financials(_FIXTURE)


def _build_market_report(market_cap: float = 2_800_000_000_000.0) -> MarketReport:
    return MarketReport(
        ticker="AAPL",
        stub=False,
        price_current=MetricValue(value=182.52, unit="USD", period="2026-04-21", source="Polygon"),
        market_cap=MetricValue(value=market_cap, unit="USD", period="2026-04-21", source="Polygon"),
    )


# ---------------------------------------------------------------------------
# Tests: _two_stage_dcf
# ---------------------------------------------------------------------------

def test_dcf_positive_fcf_returns_positive_value():
    val = _two_stage_dcf(
        base_fcf=100e9,
        near_term_growth=0.10,
        terminal_growth=0.025,
        wacc=0.09,
        years=10,
    )
    assert val > 0


def test_dcf_higher_growth_gives_higher_value():
    """A igualdad de WACC, más crecimiento → más valor."""
    low = _two_stage_dcf(100e9, 0.05, 0.025, 0.09, 10)
    high = _two_stage_dcf(100e9, 0.15, 0.025, 0.09, 10)
    assert high > low


def test_dcf_higher_wacc_gives_lower_value():
    """A igualdad de growth, más WACC → menos valor (más descuento)."""
    low_wacc = _two_stage_dcf(100e9, 0.10, 0.025, 0.08, 10)
    high_wacc = _two_stage_dcf(100e9, 0.10, 0.025, 0.12, 10)
    assert low_wacc > high_wacc


def test_dcf_wacc_leq_terminal_growth_doesnt_crash():
    """wacc <= terminal_growth es un edge case — el código debe manejarlo sin crash."""
    val = _two_stage_dcf(100e9, 0.10, 0.09, 0.09, 10)  # wacc == terminal_growth
    assert val > 0


def test_dcf_zero_fcf_returns_zero():
    val = _two_stage_dcf(0.0, 0.10, 0.025, 0.09, 10)
    assert val == 0.0


# ---------------------------------------------------------------------------
# Tests: _reverse_dcf
# ---------------------------------------------------------------------------

def test_reverse_dcf_round_trips():
    """reverse_dcf(base_fcf, dcf(g), ...) debe devolver g (aprox)."""
    base_fcf = 100e9
    g = 0.10
    val = _two_stage_dcf(base_fcf, g, 0.025, 0.09, 10)
    implied_g = _reverse_dcf(base_fcf, val, 0.025, 0.09, 10)
    assert implied_g is not None
    assert abs(implied_g - g) < 0.001  # precisión < 0.1%


def test_reverse_dcf_negative_fcf_returns_none():
    result = _reverse_dcf(-50e9, 2_000e9, 0.025, 0.09, 10)
    assert result is None


def test_reverse_dcf_zero_market_cap_returns_none():
    result = _reverse_dcf(100e9, 0.0, 0.025, 0.09, 10)
    assert result is None


def test_reverse_dcf_expensive_stock_implies_high_growth():
    """Precio muy caro vs FCF implica un crecimiento alto."""
    base_fcf = 10e9
    expensive_market_cap = 1_000e9  # 100× FCF
    cheap_market_cap = 100e9       # 10× FCF
    g_expensive = _reverse_dcf(base_fcf, expensive_market_cap, 0.025, 0.09, 10)
    g_cheap = _reverse_dcf(base_fcf, cheap_market_cap, 0.025, 0.09, 10)
    assert g_expensive is not None and g_cheap is not None
    assert g_expensive > g_cheap


# ---------------------------------------------------------------------------
# Tests: _sensitivity_table
# ---------------------------------------------------------------------------

def test_sensitivity_table_shape():
    table = _sensitivity_table(100e9, 0.025, 10, 0.09, 0.10)
    assert len(table) == 3  # 3 filas de growth
    for row in table:
        assert "near_term_growth_pct" in row
        assert "values" in row
        assert len(row["values"]) == 3  # 3 columnas de WACC


def test_sensitivity_table_higher_growth_row_has_higher_values():
    """La fila de mayor growth debe tener valores DCF más altos."""
    table = _sensitivity_table(100e9, 0.025, 10, 0.09, 0.10)
    # table[0] = growth_center + 0.05 (mayor), table[2] = growth_center - 0.05 (menor)
    high_row_values = list(table[0]["values"].values())
    low_row_values = list(table[2]["values"].values())
    assert all(h > l for h, l in zip(high_row_values, low_row_values))


# ---------------------------------------------------------------------------
# Tests: _fcf_cagr
# ---------------------------------------------------------------------------

def test_fcf_cagr_two_points():
    series = [
        MetricValue(value=121e9, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=100e9, unit="USD", period="FY2024", source="calculated"),
    ]
    cagr = _fcf_cagr(series)
    assert cagr is not None
    assert abs(cagr - 0.21) < 0.001  # 21% crecimiento


def test_fcf_cagr_single_point_returns_none():
    series = [MetricValue(value=100e9, unit="USD", period="FY2025", source="calculated")]
    assert _fcf_cagr(series) is None


def test_fcf_cagr_negative_oldest_returns_none():
    series = [
        MetricValue(value=100e9, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=-10e9, unit="USD", period="FY2024", source="calculated"),
    ]
    assert _fcf_cagr(series) is None


# ---------------------------------------------------------------------------
# Tests: valuation_agent nodo LangGraph
# ---------------------------------------------------------------------------

def test_valuation_agent_returns_valuation_key():
    """El nodo devuelve un dict con la clave 'valuation'."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    result = valuation_agent(state)
    assert "valuation" in result


def test_valuation_agent_stub_false():
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    result = valuation_agent(state)
    assert result["valuation"].stub is False


def test_valuation_agent_dcf_value_positive():
    """AAPL tiene FCF positivo → DCF debe dar equity value positivo."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    report: ValuationReport = valuation_agent(state)["valuation"]
    assert report.dcf_intrinsic_value is not None
    assert report.dcf_intrinsic_value.value > 0


def test_valuation_agent_multiples_positive():
    """Con market cap y EBIT positivos, los múltiplos deben ser positivos."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    report: ValuationReport = valuation_agent(state)["valuation"]
    assert report.ev_ebit_multiple is not None
    assert report.ev_ebit_multiple.value > 0
    assert report.ev_fcf_multiple is not None
    assert report.ev_fcf_multiple.value > 0
    assert report.pe_multiple is not None
    assert report.pe_multiple.value > 0


def test_valuation_agent_earnings_yield_positive():
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    report = valuation_agent(state)["valuation"]
    assert report.earnings_yield_pct is not None
    assert report.earnings_yield_pct > 0


def test_valuation_agent_sensitivity_table_populated():
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    report = valuation_agent(state)["valuation"]
    assert len(report.sensitivity_table) == 3


def test_valuation_agent_assumptions_preserved():
    """Los assumptions del usuario se guardan en el report."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
        "checkpoint2_user_input": {"wacc_pct": 11.0, "terminal_growth_pct": 3.0, "projection_years": 8},
    }
    report = valuation_agent(state)["valuation"]
    assert report.assumptions is not None
    assert report.assumptions.wacc_pct == 11.0
    assert report.assumptions.terminal_growth_pct == 3.0
    assert report.assumptions.projection_years == 8


def test_valuation_agent_upside_pct_calculated():
    """dcf_upside_pct se calcula cuando market_cap está disponible."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(market_cap=2_800e9),
    }
    report = valuation_agent(state)["valuation"]
    assert report.dcf_upside_pct is not None
    # El upside puede ser positivo o negativo dependiendo del precio


def test_valuation_agent_reverse_dcf_present():
    """reverse_dcf_implied_growth está presente con FCF y market_cap disponibles."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        "market": _build_market_report(),
    }
    report = valuation_agent(state)["valuation"]
    assert report.reverse_dcf_implied_growth is not None


# ---------------------------------------------------------------------------
# Tests: degraded mode
# ---------------------------------------------------------------------------

def test_valuation_agent_no_market_data():
    """Sin market data, DCF sigue calculándose pero múltiplos son None."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
        # sin market
    }
    report = valuation_agent(state)["valuation"]
    assert report.stub is False
    assert report.dcf_intrinsic_value is not None  # solo necesita FCF
    assert report.ev_ebit_multiple is None         # necesita market_cap
    assert report.pe_multiple is None
    assert report.reverse_dcf_implied_growth is None


def test_valuation_agent_no_financials():
    """Sin financials, el report es parcial pero no falla."""
    state = {
        "ticker": "AAPL",
        "market": _build_market_report(),
    }
    report = valuation_agent(state)["valuation"]
    assert report.stub is False
    assert report.dcf_intrinsic_value is None  # necesita FCF


def test_valuation_agent_empty_state():
    """Con solo ticker, el agente produce un ValuationReport vacío sin fallar."""
    report = valuation_agent({"ticker": "AAPL"})["valuation"]
    assert isinstance(report, ValuationReport)
    assert report.stub is False
    assert report.dcf_intrinsic_value is None


def test_valuation_agent_uses_defaults_when_no_checkpoint2():
    """Si no hay checkpoint2_user_input, usa WACC=9%, terminal_growth=2.5%."""
    state = {
        "ticker": "AAPL",
        "financials": _build_financials(),
    }
    report = valuation_agent(state)["valuation"]
    assert report.assumptions is not None
    assert report.assumptions.wacc_pct == 9.0
    assert report.assumptions.terminal_growth_pct == 2.5


# ---------------------------------------------------------------------------
# Tests: checkpoint2_node
# ---------------------------------------------------------------------------

def test_checkpoint2_raises_outside_runnable_context():
    """checkpoint2_node llamado directamente lanza RuntimeError (igual que checkpoint1)."""
    from due_diligence.agents.checkpoint import checkpoint2_node
    with pytest.raises(RuntimeError, match="runnable context"):
        checkpoint2_node({"ticker": "AAPL"})
