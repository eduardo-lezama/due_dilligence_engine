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
    _fcf_cagr_with_window,
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


def test_dcf_wacc_leq_terminal_growth_raises():
    """wacc <= terminal_growth: fail loud (EC-76). Antes se recortaba g a wacc−0.5pp en silencio
    y el TV salía ~200× el FCF; ahora ValuationInputError y el agente lo convierte en error visible."""
    from due_diligence.agents.valuation_math import ValuationInputError
    with pytest.raises(ValuationInputError):
        _two_stage_dcf(100e9, 0.10, 0.09, 0.09, 10)  # wacc == terminal_growth


def test_sensitivity_invalid_cell_none_but_valid_neighbours():
    """WACC centro 4.5 % − 2pp = 2.5 % = g → celda None ("n/m"), no excepción que tumbe el nodo."""
    t = _sensitivity_table(100e6, 0.025, 10, 0.045, 0.10, net_claims=0.0, share_count=1e6)
    assert t[0]["values"]["wacc_2.5pct"] is None
    assert t[0]["values"]["wacc_4.5pct"] is not None   # spread exacto de 2pp: válido


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
    table = _sensitivity_table(100e9, 0.025, 10, 0.09, 0.10, net_claims=0.0, share_count=1e9)
    assert len(table) == 3  # 3 filas de growth
    for row in table:
        assert "near_term_growth_pct" in row
        assert "values" in row
        assert len(row["values"]) == 3  # 3 columnas de WACC


def test_sensitivity_table_higher_growth_row_has_higher_values():
    """La fila de mayor growth debe tener valores DCF más altos."""
    table = _sensitivity_table(100e9, 0.025, 10, 0.09, 0.10, net_claims=0.0, share_count=1e9)
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
# Tests: _fcf_cagr_with_window (EC-60, MCE-28)
# ---------------------------------------------------------------------------

class TestFcfCagrWithWindow:
    """
    _fcf_cagr_with_window escanea desde el FCF más antiguo hacia adelante buscando
    el primer valor positivo como denominador, evitando el fallback silencioso a
    revenue CAGR cuando el año más antiguo era negativo (EC-60).
    """

    def test_all_positive_uses_full_window(self):
        """Serie completamente positiva → misma ventana que _fcf_cagr."""
        series = [
            MetricValue(value=523e6, unit="USD", period="FY2026", source="calculated"),
            MetricValue(value=421e6, unit="USD", period="FY2025", source="calculated"),
            MetricValue(value=303e6, unit="USD", period="FY2024", source="calculated"),
            MetricValue(value=146e6, unit="USD", period="FY2023", source="calculated"),
        ]
        cagr, n = _fcf_cagr_with_window(series)
        assert cagr is not None
        assert n == 3  # ventana de 3 años: FY2023→FY2026
        # 523/146 ^ (1/3) - 1 ≈ 53%
        assert abs(cagr - ((523 / 146) ** (1 / 3) - 1)) < 0.001

    def test_negative_oldest_uses_shorter_window(self):
        """
        Caso PATH: FY2022 negativo, FY2023+ positivo.
        Debe usar FY2023 como denominador en lugar de caer a None.
        """
        series = [
            MetricValue(value=523e6, unit="USD", period="FY2026", source="calculated"),
            MetricValue(value=421e6, unit="USD", period="FY2025", source="calculated"),
            MetricValue(value=303e6, unit="USD", period="FY2024", source="calculated"),
            MetricValue(value=146e6, unit="USD", period="FY2023", source="calculated"),
            MetricValue(value=-12e6, unit="USD", period="FY2022", source="calculated"),
        ]
        cagr, n = _fcf_cagr_with_window(series)
        assert cagr is not None
        assert n == 3  # ventana FY2023→FY2026 (salta FY2022 negativo)
        assert cagr > 0

    def test_all_negative_returns_none(self):
        """Sin ningún punto positivo en la cola → (None, 0)."""
        series = [
            MetricValue(value=50e6, unit="USD", period="FY2025", source="calculated"),
            MetricValue(value=-20e6, unit="USD", period="FY2024", source="calculated"),
            MetricValue(value=-50e6, unit="USD", period="FY2023", source="calculated"),
        ]
        cagr, n = _fcf_cagr_with_window(series)
        assert cagr is None
        assert n == 0

    def test_backward_compat_fcf_cagr_wrapper(self):
        """_fcf_cagr sigue funcionando como wrapper (no rompe tests existentes)."""
        series = [
            MetricValue(value=121e9, unit="USD", period="FY2025", source="calculated"),
            MetricValue(value=100e9, unit="USD", period="FY2024", source="calculated"),
        ]
        cagr_old = _fcf_cagr(series)
        cagr_new, _ = _fcf_cagr_with_window(series)
        assert cagr_old == cagr_new


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
# Tests: P5 — CAGR cyclical peak base qualifier
# ---------------------------------------------------------------------------

def _mv(value: float, period: str = "FY2025", unit: str = "USD", source: str = "XBRL") -> "MetricValue":
    """Helper para construir MetricValue en tests de forma concisa."""
    return MetricValue(value=value, unit=unit, period=period, source=source)


def _build_financials_with_peak_revenue():
    """
    Construye un FinancialsReport con revenue en el año más reciente > mediana × 1.15.
    Simula una empresa tipo CGNX: boom en el año más reciente tras varios años normales.
    """
    from due_diligence.schemas.base import FinancialsReport
    revenues = [
        _mv(900e6, "FY2025"),   # boom: +50% vs mediana ~580M
        _mv(600e6, "FY2024"),
        _mv(580e6, "FY2023"),
        _mv(560e6, "FY2022"),
        _mv(540e6, "FY2021"),
    ]
    return FinancialsReport(
        ticker="PEAK",
        cik="0001234567",
        as_of_date="2025-09-30",
        stub=False,
        source_filing="10-K FY2025",
        revenue=revenues,
        gross_profit=[_mv(400e6)],
        operating_income=[_mv(100e6)],
        net_income=[_mv(80e6)],
        operating_cash_flow=[_mv(180e6)],
        capex=[_mv(20e6)],
        free_cash_flow=[_mv(200e6), _mv(150e6, "FY2024")],
        total_assets=_mv(1_200e6),
        total_debt=_mv(200e6),
        cash_and_equivalents=_mv(300e6),
        shares_outstanding=_mv(100e6, unit="shares", source="XBRL"),
    )


def _build_financials_with_normal_revenue():
    """Revenue sin picos — más reciente está dentro del rango normal (< mediana × 1.15)."""
    from due_diligence.schemas.base import FinancialsReport
    revenues = [
        _mv(610e6, "FY2025"),
        _mv(600e6, "FY2024"),
        _mv(590e6, "FY2023"),
        _mv(580e6, "FY2022"),
        _mv(570e6, "FY2021"),
    ]
    return FinancialsReport(
        ticker="NORM",
        cik="0001234568",
        as_of_date="2025-09-30",
        stub=False,
        source_filing="10-K FY2025",
        revenue=revenues,
        gross_profit=[_mv(260e6)],
        operating_income=[_mv(80e6)],
        net_income=[_mv(60e6)],
        operating_cash_flow=[_mv(110e6)],
        capex=[_mv(10e6)],
        free_cash_flow=[_mv(120e6), _mv(110e6, "FY2024")],
        total_assets=_mv(800e6),
        total_debt=_mv(100e6),
        cash_and_equivalents=_mv(150e6),
        shares_outstanding=_mv(50e6, unit="shares", source="XBRL"),
    )


def test_valuation_agent_cyclical_peak_qualifier_present_when_boom_year():
    """P5: si revenue reciente > mediana × 1.15, el dcf_reliability_note incluye el aviso."""
    state = {
        "ticker": "PEAK",
        "financials": _build_financials_with_peak_revenue(),
        "market": _build_market_report(market_cap=500e6),
    }
    report = valuation_agent(state)["valuation"]
    assert report.dcf_reliability_note is not None
    assert "Cyclical peak" in report.dcf_reliability_note


def test_valuation_agent_no_cyclical_peak_qualifier_for_normal_revenue():
    """P5: si revenue es estable (< mediana × 1.15), NO aparece el aviso de peak base."""
    state = {
        "ticker": "NORM",
        "financials": _build_financials_with_normal_revenue(),
        "market": _build_market_report(market_cap=300e6),
    }
    report = valuation_agent(state)["valuation"]
    # El note puede existir por otras razones, pero NO debe contener "Cyclical peak"
    if report.dcf_reliability_note:
        assert "Cyclical peak" not in report.dcf_reliability_note


def test_valuation_agent_no_cyclical_peak_when_user_overrides_growth():
    """P5: si el usuario especifica growth_override, el qualifier no se activa."""
    state = {
        "ticker": "PEAK",
        "financials": _build_financials_with_peak_revenue(),
        "market": _build_market_report(market_cap=500e6),
        "checkpoint2_user_input": {
            "wacc_pct": 10.0,
            "terminal_growth_pct": 2.5,
            "projection_years": 10,
            "revenue_growth_yr1_pct": 8.0,  # usuario override → no activar qualifier
        },
    }
    report = valuation_agent(state)["valuation"]
    # Con user override, el qualifier de peak base no debe aparecer
    if report.dcf_reliability_note:
        assert "Cyclical peak" not in report.dcf_reliability_note


# ---------------------------------------------------------------------------
# Tests: checkpoint2_node
# ---------------------------------------------------------------------------

def test_checkpoint2_raises_outside_runnable_context():
    """checkpoint2_node llamado directamente lanza RuntimeError (igual que checkpoint1)."""
    from due_diligence.agents.checkpoint import checkpoint2_node
    with pytest.raises(RuntimeError, match="runnable context"):
        checkpoint2_node({"ticker": "AAPL"})


# ---------------------------------------------------------------------------
# Tests: Fix B — revenue CAGR como techo del near-term growth (EC-15)
# ---------------------------------------------------------------------------

def test_valuation_agent_fcf_cagr_capped_by_revenue_cagr():
    """
    Cuando FCF CAGR > revenue CAGR, el agente usa el revenue CAGR como techo.

    Escenario: FCF salta de $20M a $40M (CAGR=100%), revenue CAGR=8%.
    El near-term growth resultante debe ser ~8%, no 100%.
    El sensitivity table center debe reflejar ~8%, no valores absurdos.
    """
    financials = _build_financials()
    # Sobreescribir FCF series con dos puntos: $20M → $40M (CAGR=100% en 1 año)
    financials.free_cash_flow = [
        MetricValue(value=40_000_000, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=20_000_000, unit="USD", period="FY2024", source="calculated"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "market": _build_market_report(market_cap=500_000_000),
        "historical_cagr_5y": 0.08,  # 8% revenue CAGR
    }
    report = valuation_agent(state)["valuation"]
    # El sensitivity center debe ser ≤ 8% (revenue CAGR actúa como techo)
    assert report.sensitivity_table is not None and len(report.sensitivity_table) == 3
    center_growth = report.sensitivity_table[1]["near_term_growth_pct"]  # fila central
    assert center_growth <= 8.5  # margen de 0.5pp por redondeo


def test_valuation_agent_no_revenue_cagr_uses_fcf_cagr():
    """
    Sin historial de ventas (ni serie para la tendencia ni historical_cagr_5y), el agente usa
    el CAGR del FCF (ADR-043: último recurso antes del 7 % genérico).
    """
    financials = _build_financials()
    # ADR-043: con ≥ 3 años de ventas manda la tendencia; aquí se prueba el camino sin serie suficiente.
    financials.revenue = financials.revenue[:1]
    financials.free_cash_flow = [
        MetricValue(value=110_000_000, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=100_000_000, unit="USD", period="FY2024", source="calculated"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "market": _build_market_report(market_cap=500_000_000),
        # sin historical_cagr_5y
    }
    report = valuation_agent(state)["valuation"]
    # FCF CAGR = 10% → sensitivity center debe ser ~10%
    assert report.sensitivity_table is not None
    center_growth = report.sensitivity_table[1]["near_term_growth_pct"]
    assert abs(center_growth - 10.0) < 0.5


# ---------------------------------------------------------------------------
# Tests: Fix C — flag de contradicción DCF (EC-16)
# ---------------------------------------------------------------------------

def test_valuation_agent_contradiction_flag_when_large_spread():
    """
    Cuando forward growth >> reverse implied growth (>5pp), dcf_reliability_note
    debe incluir el warning de contradicción.

    Escenario: FCF=$50M, market_cap=$400M, revenue CAGR=25%.
    → near_term_growth = 25% (de revenue CAGR)
    → reverse_dcf con mkt_cap tan pequeño relativo al DCF da implied_growth << 25%
    → spread > 5pp → flag activado.
    """
    financials = _build_financials()
    financials.free_cash_flow = [
        MetricValue(value=50_000_000, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=40_000_000, unit="USD", period="FY2024", source="calculated"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "market": _build_market_report(market_cap=400_000_000),
        "historical_cagr_5y": 0.25,  # revenue CAGR 25% → near_term_growth capped a 25%
    }
    report = valuation_agent(state)["valuation"]
    # DCF@25% >> $400M → implied growth << 25% → spread > 5pp → flag
    assert report.dcf_reliability_note is not None
    assert "spread note" in report.dcf_reliability_note.lower()


def test_valuation_agent_nopat_fallback_when_fcf_negative():
    """
    EC-13: cuando FCF es negativo pero EBIT es positivo (cyclical trough o heavy
    reinvestment), el DCF debe computarse sobre NOPAT proxy (EBIT × (1-tax)) en
    lugar de devolver N/A. Caso emblemático: MOS FY2025 (FCF -$535M, EBIT $822M).
    """
    financials = _build_financials()
    # Forzar FCF negativo, mantener EBIT positivo (escenario MOS)
    financials.free_cash_flow = [
        MetricValue(value=-500_000_000, unit="USD", period="FY2025", source="calculated"),
    ]
    # ADR-033: la base del DCF es el FCFF (derivado del OCF), no el FCF reportado → el escenario
    # MOS se fuerza con OCF negativo (FCFF = −300M − capex < 0). Deuda/caja de escala MOS para que
    # el bridge no herede los 90.7B de deuda de AAPL: net claims = 1.0B − 0.2B = 0.8B, muy por
    # debajo del EV con NOPAT ≈ 800M × (1 − 18.2 %) ≈ 654M/año → equity > 0.
    financials.operating_cash_flow = [
        MetricValue(value=-300_000_000, unit="USD", period="FY2025", source="XBRL"),
    ]
    financials.total_debt = MetricValue(value=1_000_000_000, unit="USD", period="FY2025", source="XBRL")
    financials.cash_and_equivalents = MetricValue(value=200_000_000, unit="USD", period="FY2025", source="XBRL")
    financials.operating_income = [
        MetricValue(value=800_000_000, unit="USD", period="FY2025", source="XBRL"),
    ]
    financials.net_income = [
        MetricValue(value=550_000_000, unit="USD", period="FY2025", source="XBRL"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "market": _build_market_report(market_cap=5_000_000_000),
    }
    report = valuation_agent(state)["valuation"]

    # Debe computarse DCF (no N/A) usando NOPAT proxy
    assert report.dcf_intrinsic_value is not None
    assert report.dcf_intrinsic_value.value > 0
    # Source debe indicar el fallback
    assert "NOPAT" in report.dcf_intrinsic_value.source
    # Reliability note debe explicar
    assert report.dcf_reliability_note is not None
    assert "NOPAT" in report.dcf_reliability_note
    # Reverse DCF también debe estar (usa mismo base)
    assert report.reverse_dcf_implied_growth is not None
    # Sensitivity table también debe poblarse
    assert len(report.sensitivity_table) == 3


def test_valuation_agent_no_contradiction_flag_when_user_overrides_growth():
    """
    Cuando el usuario provee revenue_growth_yr1_pct manualmente, el flag de
    contradicción no se activa (el usuario es consciente de sus assumptions).
    """
    financials = _build_financials()
    financials.free_cash_flow = [
        MetricValue(value=50_000_000, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=40_000_000, unit="USD", period="FY2024", source="calculated"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "market": _build_market_report(market_cap=400_000_000),
        "checkpoint2_user_input": {"revenue_growth_yr1_pct": 25.0},  # override explícito
    }
    report = valuation_agent(state)["valuation"]
    # Con override del usuario, no se genera flag de spread
    if report.dcf_reliability_note:
        assert "spread note" not in report.dcf_reliability_note.lower()


# ---------------------------------------------------------------------------
# Tests: Fix A — _fmt_usd formatter adaptativo (EC-14)
# ---------------------------------------------------------------------------

def test_fmt_usd_small_values_show_millions():
    """Valores < $1B deben mostrarse en millones, no en billones."""
    from due_diligence.agents.value_expert import _fmt_usd
    assert _fmt_usd(39_500_000) == "$40M"
    assert _fmt_usd(500_000_000) == "$500M"
    assert _fmt_usd(1_100_000) == "$1M"


def test_fmt_usd_large_values_show_billions():
    """Valores >= $1B deben mostrarse en billones."""
    from due_diligence.agents.value_expert import _fmt_usd
    assert _fmt_usd(1_000_000_000) == "$1.0B"
    assert _fmt_usd(2_800_000_000_000) == "$2800.0B"


def test_fmt_usd_no_zero_b_for_small_caps():
    """El problema original: $39.5M no debe aparecer como $0.0B."""
    from due_diligence.agents.value_expert import _fmt_usd
    result = _fmt_usd(39_500_000)
    assert result != "$0.0B"
    assert "M" in result


# ---------------------------------------------------------------------------
# Tests: _compute_magic_formula_rank (MCE-18)
# ---------------------------------------------------------------------------

def test_compute_magic_formula_rank_assigns_unique_ranks_to_all_participants():
    """All 4 participants (subject + 3 peers) get unique ranks 1-4."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    peers = [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1),
        PeerValuation(ticker="AMZN", roic=0.158, period="FY2025", ev_ebit=22.7),
    ]
    # Subject: earnings_yield=8.13% (approx 1/12.3*100), ROIC=87.4%
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
    assert subject_rank == 1  # AAPL has highest ROIC and competitive earnings yield


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
    """Subject with earnings_yield=None (EBIT<=0) is excluded; peers still get ranks."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    peers = [
        PeerValuation(ticker="MSFT", roic=0.341, period="FY2025", ev_ebit=18.5),
        PeerValuation(ticker="GOOGL", roic=0.223, period="FY2025", ev_ebit=14.1),
    ]

    subject_rank, updated = _compute_magic_formula_rank(
        subject_ticker="AAPL",
        subject_earnings_yield=None,  # EBIT <= 0 — subject excluded
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
    """Peers with ev_ebit=None (EBIT<=0) are excluded from ranking."""
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

    valid_pv = next(pv for pv in updated if pv.ticker == "VALID")
    loss_pv = next(pv for pv in updated if pv.ticker == "LOSS_CO")
    assert loss_pv.mf_rank is None
    assert subject_rank is not None  # subject + VALID both participated
    assert valid_pv.mf_rank is not None


def test_compute_magic_formula_rank_high_roic_and_low_ev_ebit_gets_best_rank():
    """The company with best combined earnings yield AND ROIC gets rank #1."""
    from due_diligence.agents.valuation import _compute_magic_formula_rank
    from due_diligence.schemas.reports import PeerValuation

    # Subject: high ROIC (87.4%), low EV/EBIT -> high earnings yield -> should rank #1
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


# ---------------------------------------------------------------------------
# Tests: valuation_agent — Magic Formula rank wiring (MCE-18 Task 4)
# ---------------------------------------------------------------------------

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

    # AAPL has high ROIC and likely positive earnings_yield -> rank #1
    assert result["valuation"].magic_formula_rank == 1
    assert result["peer_valuations"][0].ticker == "PEER_A"
    assert result["peer_valuations"][0].mf_rank == 2


# ---------------------------------------------------------------------------
# Tests: near_term_growth transparency (EC-54)
# ---------------------------------------------------------------------------

def test_valuation_report_has_near_term_growth_fields():
    """ValuationReport debe incluir near_term_growth_used_pct y near_term_growth_source."""
    from due_diligence.schemas.reports import ValuationReport
    r = ValuationReport(ticker="TEST", stub=False)
    assert hasattr(r, "near_term_growth_used_pct")
    assert hasattr(r, "near_term_growth_source")
    assert r.near_term_growth_used_pct is None
    assert r.near_term_growth_source is None


def test_valuation_agent_source_user_override():
    """Cuando el usuario provee growth, source='user_override'."""
    state = {
        "ticker": "TEST",
        "checkpoint2_user_input": {"revenue_growth_yr1_pct": 15.0},
    }
    result = valuation_agent(state)
    r = result["valuation"]
    assert r.near_term_growth_source == "user_override"
    assert r.near_term_growth_used_pct == pytest.approx(0.15)


def test_valuation_agent_source_default_fallback():
    """Sin FCF histórico ni revenue CAGR, source='default_7pct'."""
    state = {"ticker": "TEST"}
    result = valuation_agent(state)
    r = result["valuation"]
    assert r.near_term_growth_source == "default_7pct"
    assert r.near_term_growth_used_pct == pytest.approx(0.07)


def test_valuation_agent_source_revenue_cagr_even_when_fcf_cagr_lower():
    """ADR-043: con CAGR de ventas disponible, g1 = CAGR de ventas aunque el del FCF sea menor
    (antes mín(FCF, ventas), ADR-022). La base ya es el margen normalizado × ventas."""
    financials = _build_financials()
    # ADR-043: con ≥ 3 años de ventas manda la tendencia; aquí se prueba el camino sin serie suficiente.
    financials.revenue = financials.revenue[:1]
    # FCF: $100M → $110M (CAGR ≈10%), revenue CAGR = 15% → no cap
    financials.free_cash_flow = [
        MetricValue(value=110_000_000, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=100_000_000, unit="USD", period="FY2024", source="calculated"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "historical_cagr_5y": 0.15,
    }
    result = valuation_agent(state)
    r = result["valuation"]
    assert r.near_term_growth_source == "revenue_cagr_5y"
    assert r.near_term_growth_used_pct == pytest.approx(0.15)


def test_valuation_agent_fcf_jump_does_not_leak_into_growth():
    """FCF ×2 en un año con ventas al 8 %: g1 = 8 % (CAGR de ventas), el salto del FCF no cuenta."""
    financials = _build_financials()
    # ADR-043: con ≥ 3 años de ventas manda la tendencia; aquí se prueba el camino sin serie suficiente.
    financials.revenue = financials.revenue[:1]
    # FCF: $20M → $40M (CAGR=100%), revenue CAGR=8% → cap aplicado
    financials.free_cash_flow = [
        MetricValue(value=40_000_000, unit="USD", period="FY2025", source="calculated"),
        MetricValue(value=20_000_000, unit="USD", period="FY2024", source="calculated"),
    ]
    state = {
        "ticker": "FAKE",
        "financials": financials,
        "historical_cagr_5y": 0.08,
    }
    result = valuation_agent(state)
    r = result["valuation"]
    assert r.near_term_growth_source == "revenue_cagr_5y"
    assert r.near_term_growth_used_pct == pytest.approx(0.08)


def test_valuation_agent_reliability_note_always_present():
    """dcf_reliability_note debe estar presente SIEMPRE (no solo en contradicción)."""
    state = {
        "ticker": "TEST",
        "historical_cagr_5y": 0.08,
    }
    result = valuation_agent(state)
    r = result["valuation"]
    # Con revenue CAGR como fallback, la nota debe existir
    assert r.dcf_reliability_note is not None
    assert "8.0%" in r.dcf_reliability_note or "8%" in r.dcf_reliability_note


def test_valuation_agent_reliability_note_default_has_strong_warning():
    """El fallback 7% debe tener el warning más fuerte — 'generic fallback'."""
    state = {"ticker": "TEST"}
    result = valuation_agent(state)
    r = result["valuation"]
    assert r.dcf_reliability_note is not None
    note_lower = r.dcf_reliability_note.lower()
    assert "generic" in note_lower or "fallback" in note_lower
    assert "7" in r.dcf_reliability_note


def test_compute_near_term_growth_pure_function():
    """_compute_near_term_growth es pure — testeable sin estado LangGraph."""
    from due_diligence.agents.valuation import _compute_near_term_growth
    growth, source = _compute_near_term_growth(None, None, None)
    assert growth == pytest.approx(0.07)
    assert source == "default_7pct"

    growth2, source2 = _compute_near_term_growth(None, None, 12.0)
    assert growth2 == pytest.approx(0.12)
    assert source2 == "user_override"

    growth3, source3 = _compute_near_term_growth(None, 0.09, None)
    assert growth3 == pytest.approx(0.09)
    assert source3 == "revenue_cagr_5y"


# ---------------------------------------------------------------------------
# T12a — DCF sobre FCFF → EV → bridge → por acción (ADR-033)
# Ejemplo A: OCF 260, capex 60, intereses 40, SBC 20, EBIT 300, deuda 500, caja 50,
# 10M acciones, precio 150, WACC 9 %, g 0, t 25 % (sin datos de impuestos → default 0.25).
# ---------------------------------------------------------------------------

def _levered_financials():
    from due_diligence.schemas.base import FinancialsReport
    return FinancialsReport(
        ticker="LEV", cik="0000000001", as_of_date="2025-12-31", source_filing="10-K FY2025",
        revenue=[_mv(2_000e6)], gross_profit=[_mv(800e6)],
        operating_income=[_mv(300e6)], net_income=[_mv(195e6)],
        operating_cash_flow=[_mv(260e6)], capex=[_mv(60e6)], free_cash_flow=[_mv(200e6)],
        interest_expense=[_mv(40e6)], sbc=[_mv(20e6)],              # ← campos de T8
        total_assets=_mv(3_000e6), total_debt=_mv(500e6), cash_and_equivalents=_mv(50e6),
        shares_outstanding=_mv(10e6, unit="shares"),
    )


def _levered_state():
    return {
        "ticker": "LEV", "financials": _levered_financials(),
        "market": MarketReport(ticker="LEV", stub=False,
            price_current=MetricValue(value=150.0, unit="USD", period="2026-10-01", source="test"),
            market_cap=MetricValue(value=1_500e6, unit="USD", period="2026-10-01", source="test")),
        "checkpoint2_user_input": {"wacc_pct": 9.0, "terminal_growth_pct": 0.0, "projection_years": 10,
                                   "revenue_growth_yr1_pct": 0.0, "mid_year_convention": False,
                                   "valuation_date": "2025-12-31"},   # = cierre fiscal → sin capitalización
    }


def test_fcff_dcf_subtracts_net_debt_example_a():
    # Ejemplo A con valor terminal con reinversión (NOPAT 225, g=0 → TV = 225/0.09):
    # explícito 210 × 6.417658 = 1 347.708; TV PV 2 500 × 0.4224108 = 1 056.027; EV 2 403.735
    # equity = 2 403.735 − 450 = 1 953.735 → $195.37; upside 195.37/150 − 1 = +30.2 %
    # (el método antiguo daba $222.22 sin bridge; el DCF sin reinversión terminal daría $188.33)
    r = valuation_agent(_levered_state())["valuation"]
    assert r.fcff_base.value == pytest.approx(210e6)
    assert r.enterprise_value_dcf.value == pytest.approx(2_403.735e6, rel=1e-5)
    assert r.equity_bridge.net_claims == pytest.approx(450e6)
    assert r.dcf_intrinsic_per_share.value == pytest.approx(195.37, abs=0.01)
    assert r.dcf_upside_pct == pytest.approx(30.2, abs=0.05)
    assert r.tax_rate_used == pytest.approx(0.25)
    assert r.valuation_basis.startswith("FCFF")
    assert r.terminal_value_share == pytest.approx(1_056.027 / 2_403.735, rel=1e-4)
    assert r.dcf_intrinsic_value.value == pytest.approx(1_953.735e6, rel=1e-5)   # equity tras el bridge
    assert r.share_count_used == pytest.approx(10e6)
    assert r.fcff_breakdown.after_tax_interest_addback == pytest.approx(30e6)


def test_upside_none_when_price_missing():
    st = _levered_state(); st["market"] = st["market"].model_copy(update={"price_current": None})
    r = valuation_agent(st)["valuation"]
    assert r.dcf_upside_pct is None and "price unavailable" in r.dcf_reliability_note


def test_sti_subtracted_exactly_once_end_to_end():
    """Bloqueante de la revisión adversarial: cash_and_equivalents (solo caja, T6) + STI aparte."""
    fin = _levered_financials().model_copy(update={
        "cash_and_equivalents": _mv(50e6),
        "short_term_investments": _mv(30e6),
    })
    st = _levered_state(); st["financials"] = fin
    r = valuation_agent(st)["valuation"]
    assert r.equity_bridge.cash == pytest.approx(50e6)
    assert r.equity_bridge.short_term_investments == pytest.approx(30e6)
    assert r.equity_bridge.net_claims == pytest.approx(500e6 - 50e6 - 30e6)     # 420, no 390
    assert r.market_enterprise_value.value == pytest.approx(1_500e6 + 420e6)


def test_parse_financials_to_valuation_agent_sti_once_integration():
    """Integración real parse_financials → valuation_agent con STI > 0 (fixture AAPL + STI 30B):
    la caja del bridge es SOLO caja y la STI se resta una vez."""
    import copy
    from due_diligence.tools.edgar import EdgarAdapter
    raw = copy.deepcopy(_FIXTURE)
    raw["balance_sheet"]["short_term_investments"] = {"value": 30e9, "period": "FY2025"}
    fin = EdgarAdapter.parse_financials(raw)
    r = valuation_agent({"ticker": "AAPL", "financials": fin, "market": _build_market_report()})["valuation"]
    b = r.equity_bridge
    assert b.cash == pytest.approx(fin.cash_and_equivalents.value)          # 35.934B, sin STI dentro
    assert b.short_term_investments == pytest.approx(30e9)
    # net claims = deuda 90.678B − caja 35.934B − STI 30B = 24.744B
    # Fixture AAPL regenerado (T14): también hay LTI (valores negociables no corrientes) → se suman
    # una vez. A mano: 99.887B − 35.934B − 30B (STI forzada) − 77.723B = −43.770B.
    lti = fin.long_term_investments.value if fin.long_term_investments else 0.0
    assert b.net_claims == pytest.approx(fin.total_debt.value - fin.cash_and_equivalents.value - 30e9 - lti)
    assert b.net_claims == pytest.approx(-43.770e9, rel=1e-4)


def test_valuation_agent_returns_error_report_instead_of_crashing_on_bad_rates():
    st = _levered_state(); st["checkpoint2_user_input"].update({"wacc_pct": 3.0, "terminal_growth_pct": 2.5})
    r = valuation_agent(st)["valuation"]
    assert r.stub is True and r.error.startswith("ValuationInputError") and r.dcf_intrinsic_per_share is None


def test_valuation_date_capitalization_from_fiscal_year_end():
    # 10 meses (304 días: 2025-12-31 → 2026-10-31) desde el cierre: EV × 1.09^(304/365) ≈ ×1.0744 (revisión M5)
    st = _levered_state(); st["checkpoint2_user_input"]["valuation_date"] = "2026-10-31"
    r = valuation_agent(st)["valuation"]
    assert r.enterprise_value_dcf.value == pytest.approx(2_403.735e6 * 1.09 ** (304 / 365), rel=1e-4)
    assert "capitalized" in r.dcf_reliability_note


def test_normalized_fcff_base_uses_median_margin_when_three_years():
    fin = _levered_financials().model_copy(update={
        "revenue": [_mv(2_000e6), _mv(1_800e6, "FY2024"), _mv(1_600e6, "FY2023")],
        "operating_cash_flow": [_mv(260e6), _mv(200e6, "FY2024"), _mv(150e6, "FY2023")],
        "capex": [_mv(60e6), _mv(50e6, "FY2024"), _mv(40e6, "FY2023")],
        "interest_expense": [_mv(40e6), _mv(40e6, "FY2024"), _mv(40e6, "FY2023")],
        "sbc": [_mv(20e6), _mv(18e6, "FY2024"), _mv(16e6, "FY2023")],
    })
    # FCFF por año: 260+30−60−20 = 210 (10.5 %), 200+30−50−18 = 162 (9.0 %), 150+30−40−16 = 124 (7.75 %)
    # → mediana 9.0 % × 2 000 = 180
    st = _levered_state(); st["financials"] = fin
    r = valuation_agent(st)["valuation"]
    assert r.fcff_base.value == pytest.approx(180e6)
    assert "median FCFF margin (3y)" in r.fcff_base.source


def test_loss_making_company_yields_no_dcf_with_note():
    st = _levered_state()
    st["financials"] = st["financials"].model_copy(update={
        "operating_income": [_mv(-50e6)], "net_income": [_mv(-80e6)], "operating_cash_flow": [_mv(-10e6)]})
    r = valuation_agent(st)["valuation"]
    assert r.dcf_intrinsic_per_share is None and "no positive FCFF or NOPAT" in r.dcf_reliability_note


def test_no_share_count_yields_none_per_share_with_note():
    st = _levered_state()
    st["financials"] = st["financials"].model_copy(update={"shares_outstanding": None})
    st["market"] = st["market"].model_copy(update={"price_current": None, "market_cap": None})
    r = valuation_agent(st)["valuation"]
    assert r.dcf_intrinsic_per_share is None and "share count unavailable" in r.dcf_reliability_note


def test_valuation_input_defaults_mid_year_false_when_absent():
    st = _levered_state(); st["checkpoint2_user_input"].pop("mid_year_convention")
    r = valuation_agent(st)["valuation"]
    assert r.mid_year_convention_used is False


def test_valuation_agent_default_wacc_is_suggested_real_wacc_with_market():
    """Sin WACC del usuario y con market cap: default = suggest_wacc (no 9.0 fijo)."""
    from due_diligence.agents.valuation import suggest_wacc
    st = _levered_state(); st["checkpoint2_user_input"].pop("wacc_pct")
    r = valuation_agent(st)["valuation"]
    expected = round(suggest_wacc(st["financials"], 1_500e6).wacc * 100, 1)   # 7.8 (ver test_checkpoint_socratic)
    assert r.assumptions.wacc_pct == expected == 7.8
    assert r.wacc_breakdown is not None and r.suggested_wacc_pct == 7.8


# ---------------------------------------------------------------------------
# T12b — EPV, reverse DCF, múltiplos y peers en el mismo bridge (ADR-033)
# ---------------------------------------------------------------------------

def test_epv_uses_same_bridge_example_a():
    # EPV = EBIT 300 × (1 − 25 % marginal) / 9 % = 225 / 0.09 = 2 500 (EV)
    # per share = (2 500 − 450) / 10 = 205.00; upside = 205/150 − 1 = +36.7 %
    r = valuation_agent(_levered_state())["valuation"]
    assert r.epv_enterprise.value == pytest.approx(2_500e6, rel=1e-6)
    assert r.epv_per_share.value == pytest.approx(205.00, abs=0.01)
    assert r.epv_upside_pct == pytest.approx(36.7, abs=0.05)
    # growth premium = 1 − 205/150 = −0.3667 (negativo: el precio está POR DEBAJO del EPV)
    assert r.epv_growth_premium == pytest.approx(1 - 205 / 150, abs=1e-4)


def test_reverse_dcf_targets_market_ev_not_market_cap():
    from due_diligence.agents.valuation_math import dcf_components
    r = valuation_agent(_levered_state())["valuation"]
    # market EV = 150 × 10M + net claims 450M = 1 950M
    assert r.market_enterprise_value.value == pytest.approx(1_950e6)
    # El reverse usa la MISMA función que el directo (incluida la reinversión terminal con NOPAT 225)
    ev_at_implied = dcf_components(210e6, r.reverse_dcf_implied_growth, 0.0, 0.09, 5, 5,
                                   nopat_base=225e6).enterprise_value
    assert ev_at_implied == pytest.approx(1_950e6, rel=2e-3)
    assert r.ev_ebit_multiple.value == pytest.approx(6.5)              # 1 950 / 300
    assert r.ev_fcf_multiple.value == pytest.approx(9.29, abs=0.01)    # EV/FCFF = 1 950 / 210 = 9.2857
    assert r.pe_multiple.value == pytest.approx(1_500 / 195, abs=0.01) # equity de mercado / NI = 7.69
    assert r.earnings_yield_pct == pytest.approx(300 / 1_950 * 100, abs=0.01)  # 15.38 %


def test_net_net_market_ev_nonpositive_multiples_nm():
    # Caja 2 000M > equity de mercado 1 500M + deuda 500M → market EV = 1 500 + 500 − 2 000 = 0 → n/m
    st = _levered_state()
    st["financials"] = st["financials"].model_copy(update={"cash_and_equivalents": _mv(2_000e6)})
    r = valuation_agent(st)["valuation"]
    assert r.market_enterprise_value.value == pytest.approx(0.0)
    assert r.ev_ebit_multiple is None and r.ev_fcf_multiple is None and r.reverse_dcf_implied_growth is None
    assert "Net-net" in r.dcf_reliability_note


def test_compute_epv_no_enterprise_vs_market_cap_fallback():
    # 180 × 0.75 / 0.09 = 1 500 − 200 = 1 300 / 10 = 130; sin precio → upside None (nunca EV vs market cap)
    from due_diligence.agents.valuation import _compute_epv
    _, ps, up, _, _ = _compute_epv([_mv(180e6)], 0.09, net_claims=200e6, shares=10e6, price_current=None)
    assert ps.value == pytest.approx(130.0) and up is None


def test_epv_growth_premium_reported():
    # EPV/sh 100 vs precio 150 → el mercado paga un 33 % por crecimiento (Greenwald): no es "caro", es "premium".
    # EBIT 120 × 0.75 / 0.09 = 1 000 → /10M = 100. (El plan usaba EBIT 135 → 112.5/sh, inconsistente
    # con su propio assert de 100: dato corregido.) premium = 1 − 100/150 = 1/3
    from due_diligence.agents.valuation import _compute_epv
    _, ps, up, _, premium = _compute_epv([_mv(120e6)], 0.09, net_claims=0.0, shares=10e6, price_current=150.0)
    assert ps.value == pytest.approx(150.0 * (1 - 1 / 3), rel=1e-6) and premium == pytest.approx(1 / 3, rel=1e-6)


def test_ebit_median_includes_negative_years():
    from due_diligence.agents.valuation import _ebit_median
    med, _ = _ebit_median([_mv(100e6), _mv(-50e6, "FY2024"), _mv(80e6, "FY2023")])
    assert med == pytest.approx(80e6)                       # antes se descartaba el −50 y salía 90
    med_neg, _ = _ebit_median([_mv(10e6), _mv(-50e6, "FY2024"), _mv(-80e6, "FY2023")])
    assert med_neg is None                                  # mediana ≤ 0 → EPV None


def test_reverse_dcf_none_at_search_bounds():
    # Target inalcanzable (EV de mercado 1 000× el FCFF) → la búsqueda se pega al borde 2.0 → None
    from due_diligence.agents.valuation import _reverse_dcf
    assert _reverse_dcf(1e6, 1e15, 0.025, 0.09, 10, stage1_years=5, fade_years=5) is None


# ---------------------------------------------------------------------------
# T12c — Convergencia, umbrales y sensibilidad por acción (ADR-033 §8-9)
# ---------------------------------------------------------------------------

def test_epv_classifier_symmetric_band():
    from due_diligence.agents.valuation import _classify_epv_upside
    assert _classify_epv_upside(-5.0) == "fair"        # antes: < 0 → expensive
    assert _classify_epv_upside(-10.1) == "expensive"


def test_convergence_historical_is_revenue_cagr():
    st = _levered_state(); st["historical_cagr_5y"] = 0.04
    r = valuation_agent(st)["valuation"]
    assert "historical 4.0%" in r.valuation_methods_summary[2]["display_upside"]


def test_reverse_dcf_reports_ten_year_equivalent():
    # g1 8 % durante 5 años y fade lineal a 2.5 % en 5: 6.9, 5.8, 4.7, 3.6, 2.5 %.
    # Π = 1.08^5 (1.469328) × 1.069 × 1.058 × 1.047 × 1.036 × 1.025 (1.257458) = 1.847618
    # → 1.847618^(1/10) − 1 = 6.33 % (el plan decía ≈ 6.35 %: dentro de ±0.05 pp)
    from due_diligence.agents.valuation import _ten_year_equivalent_cagr
    assert _ten_year_equivalent_cagr(0.08, 0.025, 5, 5) == pytest.approx(0.0633, abs=5e-4)


def test_reverse_classification_uses_ten_year_equivalent():
    # Ejemplo A: el reverse da g1 implícito < 0; con historical revenue CAGR 4 % → "cheap"
    st = _levered_state(); st["historical_cagr_5y"] = 0.04
    r = valuation_agent(st)["valuation"]
    row = r.valuation_methods_summary[2]
    assert r.reverse_dcf_10y_equivalent is not None
    assert "10y-equiv" in row["display_upside"] and row["classification"] == "cheap"


def test_sensitivity_per_share_and_tv_share():
    # Centro (WACC 9 %, g1 0 %) = el DCF del titular: misma base FCFF 210, misma reinversión
    # terminal con NOPAT 225 → (2 403.735 − 450) / 10 = $195.37. (El plan fijaba 188.33, que es el
    # DCF SIN reinversión terminal; la revisión adversarial manda: la tabla debe reproducir el titular.)
    r = valuation_agent(_levered_state())["valuation"]
    assert r.sensitivity_unit == "USD/share"
    center = r.sensitivity_table[1]["values"]["wacc_9.0pct"]
    assert center == pytest.approx(195.37, abs=0.01) == r.dcf_intrinsic_per_share.value
    assert 0 < r.terminal_value_share < 1


def test_sensitivity_invalid_cell_is_none():
    t = _sensitivity_table(100e6, 0.025, 10, 0.045, 0.10, net_claims=0.0, share_count=1e6)
    assert t[0]["values"]["wacc_2.5pct"] is None


def test_sensitivity_empty_without_share_count_with_note():
    st = _levered_state()
    st["financials"] = st["financials"].model_copy(update={"shares_outstanding": None})
    st["market"] = st["market"].model_copy(update={"price_current": None, "market_cap": None})
    r = valuation_agent(st)["valuation"]
    assert r.sensitivity_table == [] and "Sensitivity table not computed" in r.dcf_reliability_note
