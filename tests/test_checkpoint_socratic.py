"""
Tests para _build_socratic_questions (ADR-018).

Qué se testea:
    - Detecta mismatch moat 'narrow' con ROIC alto → pregunta sobre moat.
    - Detecta mismatch moat 'none' con ROIC moderado → pregunta sobre moat.
    - Detecta insider ownership bajo → pregunta sobre alineación.
    - Detecta CEO tenure largo (>12 años) → pregunta sobre sucesión.
    - Siempre incluye la pregunta de peers (ADR-015).
    - Degrades gracefully cuando faltan reports — no genera preguntas sin datos.

Estrategia de aislamiento:
    Zero llamadas a EDGAR, LLM ni APIs. Solo lógica heurística pura.
"""

from datetime import date

import pytest

from due_diligence.schemas.reports import CompetitiveReport, ManagementReport
from due_diligence.schemas.base import FinancialsReport, MetricValue


def _make_state(
    moat: str = "wide",
    roic: float = 0.15,
    insider_pct: float = 5.0,
    ceo_tenure: float = 5.0,
) -> dict:
    """Helper: state mínimo con competitive, financials y mgmt."""
    return {
        "ticker": "AAPL",
        "competitive": CompetitiveReport(
            ticker="AAPL",
            stub=False,
            moat_classification=moat,
            moat_sources=["brand"],
        ),
        "financials": FinancialsReport(
            ticker="AAPL",
            cik="0000320193",
            as_of_date=date(2025, 9, 28),
            revenue=[],
            gross_profit=[],
            operating_income=[],
            net_income=[],
            operating_cash_flow=[],
            capex=[],
            free_cash_flow=[],
            total_assets=MetricValue(value=1e12, unit="USD", period="FY2025", source="XBRL"),
            total_debt=MetricValue(value=1e11, unit="USD", period="FY2025", source="XBRL"),
            cash_and_equivalents=MetricValue(value=5e10, unit="USD", period="FY2025", source="XBRL"),
            shares_outstanding=MetricValue(value=1.5e10, unit="shares", period="FY2025", source="XBRL"),
            roic_series=[MetricValue(value=roic, unit="ratio", period="FY2025", source="calculated")],
            source_filing="10-K FY2025",
        ),
        "mgmt": ManagementReport(
            ticker="AAPL",
            stub=False,
            insider_ownership_pct=insider_pct,
            ceo_tenure_years=ceo_tenure,
        ),
    }


def test_socratic_no_peer_question_in_socratic():
    """La pregunta de peers NO debe aparecer en las Socratic — tiene su propio prompt dedicado."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions({"ticker": "AAPL"})
    # Sin datos de especialistas, la lista debe estar vacía (no hay conflictos que generar)
    assert len(qs) == 0


def test_socratic_detects_narrow_moat_high_roic():
    """Moat 'narrow' con ROIC >20% genera pregunta de reconciliación."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions(_make_state(moat="narrow", roic=0.25))
    moat_qs = [q for q in qs if "narrow" in q.lower() or "roic" in q.lower() or "25" in q]
    assert len(moat_qs) >= 1


def test_socratic_detects_no_moat_moderate_roic():
    """Moat 'none' con ROIC >15% genera pregunta de reconciliación."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions(_make_state(moat="none", roic=0.18))
    moat_qs = [q for q in qs if "moat" in q.lower() or "roic" in q.lower() or "none" in q.lower()]
    assert len(moat_qs) >= 1


def test_socratic_detects_low_insider_ownership():
    """Insider ownership <1% genera pregunta de alineación."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions(_make_state(insider_pct=0.03))
    ownership_qs = [q for q in qs if "insider" in q.lower() or "ownership" in q.lower()]
    assert len(ownership_qs) >= 1


def test_socratic_detects_long_ceo_tenure():
    """CEO tenure >12 años genera pregunta sobre sucesión."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions(_make_state(ceo_tenure=14.0))
    tenure_qs = [q for q in qs if "ceo" in q.lower() or "tenure" in q.lower() or "14" in q]
    assert len(tenure_qs) >= 1


def test_socratic_no_moat_question_for_wide_moat_high_roic():
    """Moat 'wide' con ROIC alto es consistente — no genera pregunta de moat."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions(_make_state(moat="wide", roic=0.87))
    moat_conflict_qs = [q for q in qs if "narrow" in q.lower() or "none" in q.lower()]
    assert len(moat_conflict_qs) == 0


def test_socratic_skips_patterns_without_data():
    """Sin competitive ni mgmt en el state, no genera preguntas (no hay conflictos)."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions({"ticker": "XYZ"})
    assert len(qs) == 0


# ---------------------------------------------------------------------------
# Checkpoint 2 (T11): WACC real sugerido, defaults únicos y validación en el nodo
# ---------------------------------------------------------------------------

from due_diligence.agents.checkpoint import (  # noqa: E402
    CHECKPOINT2_DEFAULTS, _build_checkpoint2_summary, checkpoint2_node,
)
from due_diligence.agents.valuation import suggest_wacc  # noqa: E402
from due_diligence.schemas.reports import MarketReport  # noqa: E402


def _mv2(v, p="FY2025", u="USD"):
    return MetricValue(value=v, unit=u, period=p, source="test")


@pytest.fixture
def levered_state_with_market():
    """Ejemplo A de ADR-033: EBIT 300M, intereses 40M, deuda 500M, caja 50M, market cap 1.5B."""
    fin = FinancialsReport(
        ticker="LEV", cik="0000000001", as_of_date=date(2025, 12, 31), source_filing="10-K FY2025",
        revenue=[_mv2(2_000e6)], gross_profit=[_mv2(800e6)], operating_income=[_mv2(300e6)],
        net_income=[_mv2(195e6)], operating_cash_flow=[_mv2(260e6)], capex=[_mv2(60e6)],
        free_cash_flow=[_mv2(200e6)], interest_expense=[_mv2(40e6)], sbc=[_mv2(20e6)],
        total_assets=_mv2(3_000e6), total_debt=_mv2(500e6), cash_and_equivalents=_mv2(50e6),
        shares_outstanding=_mv2(10e6, u="shares"),
    )
    market = MarketReport(ticker="LEV", stub=False,
                          price_current=_mv2(150.0, "2026-10-01"), market_cap=_mv2(1_500e6, "2026-10-01"))
    return {"ticker": "LEV", "financials": fin, "market": market}


def test_checkpoint2_summary_proposes_real_wacc(levered_state_with_market):
    # Ke 9.2 % (prima de tamaño OFF); cobertura 300/40 = 7.5× → AA → Kd 5.0 %; E 1 500 / D 500
    # WACC = 0.75×9.2 % + 0.25×5.0 %×0.75 = 6.9 % + 0.9375 % = 7.8375 % → 7.8
    s = _build_checkpoint2_summary(levered_state_with_market)
    expected = suggest_wacc(levered_state_with_market["financials"], 1_500e6)
    assert s["defaults"]["wacc_pct"] == round(expected.wacc * 100, 1) == 7.8
    assert "wacc_breakdown" in s["context"] and "Kd 5.0%" in s["context"]["wacc_breakdown"]


def test_checkpoint2_summary_falls_back_to_default_wacc_without_market_cap(levered_state_with_market):
    st = dict(levered_state_with_market)
    st["market"] = st["market"].model_copy(update={"market_cap": None})
    s = _build_checkpoint2_summary(st)
    assert s["defaults"]["wacc_pct"] == CHECKPOINT2_DEFAULTS["wacc_pct"]
    assert "unavailable" in s["context"]["wacc_breakdown"]


def test_checkpoint2_defaults_single_source():
    assert CHECKPOINT2_DEFAULTS["terminal_growth_pct"] == 2.5
    assert CHECKPOINT2_DEFAULTS["projection_years"] == 10
    assert CHECKPOINT2_DEFAULTS["mid_year_convention"] is False


def test_checkpoint2_node_reinterrupts_on_invalid_rates(mocker, levered_state_with_market):
    """Un caller programático (o la futura web) no puede colar WACC ≤ g: el nodo vuelve a interrumpir
    con "error" en el payload hasta recibir un resume válido."""
    calls = []

    def fake_interrupt(payload):
        calls.append(payload)
        return [{"wacc_pct": 2.0, "terminal_growth_pct": 2.5}, {"wacc_pct": 9.0, "terminal_growth_pct": 2.5}][len(calls) - 1]

    mocker.patch("due_diligence.agents.checkpoint.interrupt", side_effect=fake_interrupt)
    out = checkpoint2_node(levered_state_with_market)
    assert out["checkpoint2_user_input"]["wacc_pct"] == 9.0
    assert len(calls) == 2 and "error" not in calls[0] and "ValuationInputError" in calls[1]["error"]


def test_checkpoint2_node_accepts_empty_resume(mocker, levered_state_with_market):
    """Resume sin WACC → el agente usará el WACC sugerido; nada que validar contra g por defecto."""
    mocker.patch("due_diligence.agents.checkpoint.interrupt", return_value={"proceed": True})
    assert checkpoint2_node(levered_state_with_market)["checkpoint2_user_input"] == {"proceed": True}


def test_checkpoint2_node_real_langgraph_interrupt_roundtrip(levered_state_with_market):
    """Verificación contra LangGraph 1.1.8 real (R4): el payload llega en "__interrupt__" del
    resultado de invoke(); un resume inválido produce un SEGUNDO interrupt con "error"; un resume
    válido completa el nodo. Grafo mínimo de un solo nodo, MemorySaver, sin red."""
    from typing import Any, TypedDict

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    from due_diligence.cli import _pending_interrupt_payload

    class _S(TypedDict, total=False):
        ticker: str
        financials: Any
        market: Any
        checkpoint2_user_input: dict

    b = StateGraph(_S)
    b.add_node("cp2", checkpoint2_node)
    b.add_edge(START, "cp2")
    b.add_edge("cp2", END)
    g = b.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-cp2"}}

    first = _pending_interrupt_payload(g.invoke(levered_state_with_market, config=cfg), g, cfg)
    assert first["phase"] == "checkpoint2" and first["defaults"]["wacc_pct"] == 7.8 and "error" not in first

    second = _pending_interrupt_payload(
        g.invoke(Command(resume={"wacc_pct": 2.0, "terminal_growth_pct": 2.5}), config=cfg), g, cfg)
    assert second is not None and "ValuationInputError" in second["error"]

    done = g.invoke(Command(resume={"wacc_pct": 9.0, "terminal_growth_pct": 2.5}), config=cfg)
    assert _pending_interrupt_payload(done, g, cfg) is None
    assert done["checkpoint2_user_input"]["wacc_pct"] == 9.0



def test_checkpoint2_summary_small_cap_hints_use_adaptive_formatter():
    """EC-86: el checkpoint 2 mostraba '$0.0B' de FCF y '$0B' de market cap en small caps,
    justo donde el usuario decide WACC y crecimiento. Debe usar fmt_usd (EC-14/EC-17)."""
    from due_diligence.agents.checkpoint import _build_checkpoint2_summary
    from due_diligence.schemas.base import FinancialsReport, MetricValue
    from due_diligence.schemas.reports import MarketReport

    mv = lambda v, p="FY2025", u="USD": MetricValue(value=v, unit=u, period=p, source="test")
    fin = FinancialsReport(
        ticker="BBW", cik="1", as_of_date="2026-01-31", source_filing="10-K FY2026",
        revenue=[mv(529.8e6)], gross_profit=[mv(290e6)], operating_income=[mv(60e6)], net_income=[mv(47e6)],
        operating_cash_flow=[mv(65e6)], capex=[mv(25.4e6)], free_cash_flow=[mv(39.6e6, "FY2026")],
        total_assets=mv(345e6), total_debt=mv(0.0), cash_and_equivalents=mv(26.8e6),
        shares_outstanding=mv(12.58e6, u="shares"),
    )
    market = MarketReport(ticker="BBW", stub=False,
                          price_current=mv(50.0, "2026-10-01", "USD/share"),
                          market_cap=mv(629e6, "2026-10-01"))
    s = _build_checkpoint2_summary({"ticker": "BBW", "financials": fin, "market": market})
    assert s["context"]["latest_fcf"] == "$40M (FY2026)"
    assert s["context"]["market_cap"] == "$629M"
