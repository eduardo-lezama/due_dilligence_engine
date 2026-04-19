"""
Tests para AnalysisState — la "pizarra" compartida del grafo LangGraph.

Verifican que:
1. El estado es válido con solo `ticker` (todos los demás campos son NotRequired).
2. El estado acepta todos los campos posibles cuando están presentes.
"""

from datetime import date


def test_analysis_state_minimal_invocation():
    """Estado válido con solo ticker — todos los demás campos son NotRequired."""
    from due_diligence.graph.state import AnalysisState

    # Con NotRequired, Python no valida en runtime — solo typing.
    # Este dict es un AnalysisState válido porque ticker está presente.
    state: AnalysisState = {"ticker": "AAPL"}
    assert state["ticker"] == "AAPL"
    # Los campos NotRequired no existen hasta que un nodo los escribe
    assert "financials" not in state


def test_analysis_state_with_all_fields():
    """Estado con todos los campos posibles — simula grafo completo."""
    from due_diligence.graph.state import AnalysisState
    from due_diligence.schemas.base import FinancialsReport, MetricValue

    def _mv(v: float, period: str) -> MetricValue:
        """Helper para crear MetricValue con defaults de test."""
        return MetricValue(value=v, unit="USD", period=period, source="XBRL")

    financials = FinancialsReport(
        ticker="AAPL", cik="0000320193", as_of_date=date(2024, 9, 28),
        revenue=[_mv(391e9, "FY2024")], gross_profit=[_mv(180e9, "FY2024")],
        operating_income=[_mv(123e9, "FY2024")], net_income=[_mv(93e9, "FY2024")],
        operating_cash_flow=[_mv(118e9, "FY2024")], capex=[_mv(9e9, "FY2024")],
        free_cash_flow=[_mv(109e9, "FY2024")],
        total_assets=_mv(364e9, "FY2024"), total_debt=_mv(96e9, "FY2024"),
        cash_and_equivalents=_mv(65e9, "FY2024"),
        shares_outstanding=MetricValue(value=15e9, unit="shares", period="FY2024", source="XBRL"),
        source_filing="10-K FY2024",
    )

    state: AnalysisState = {
        "ticker": "AAPL",
        "cik": "0000320193",
        "as_of_date": "2024-09-28",
        "run_id": "test-run-001",
        "financials": financials,
        "citations": [],
        "unverified_claims": [],
        "cost_usd": 0.0,
    }
    assert state["cik"] == "0000320193"
    assert state["financials"].ticker == "AAPL"
