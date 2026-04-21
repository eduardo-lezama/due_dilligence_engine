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


def test_socratic_always_includes_peer_prompt():
    """La pregunta de peers siempre está presente independientemente del state."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions({"ticker": "AAPL"})
    assert any("peer" in q.lower() for q in qs)


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
    """Sin competitive ni mgmt en el state, solo genera la pregunta de peers."""
    from due_diligence.agents.checkpoint import _build_socratic_questions

    qs = _build_socratic_questions({"ticker": "XYZ"})
    assert len(qs) == 1
    assert "peer" in qs[0].lower()
