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
    Sin historical_cagr_5y en estado, el agente usa FCF CAGR sin modificación
    (manteniendo comportamiento anterior cuando no hay revenue CAGR disponible).
    """
    financials = _build_financials()
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


def test_valuation_agent_source_fcf_cagr():
    """Con FCF histórico positivo y FCF CAGR < revenue CAGR, source contiene 'fcf_cagr'."""
    financials = _build_financials()
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
    assert r.near_term_growth_source is not None
    assert "fcf_cagr" in r.near_term_growth_source
    assert "rev_capped" not in r.near_term_growth_source


def test_valuation_agent_source_fcf_cagr_rev_capped():
    """Cuando FCF CAGR > revenue CAGR, source contiene 'rev_capped'."""
    financials = _build_financials()
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
    assert r.near_term_growth_source is not None
    assert "rev_capped" in r.near_term_growth_source


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
    assert source3 == "revenue_cagr_fallback"
