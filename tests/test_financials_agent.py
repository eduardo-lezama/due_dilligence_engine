"""
Tests para el Financials specialist node.

Verifican:
- El nodo devuelve un FinancialsReport válido usando el fixture cacheado.
- Revenue y FCF tienen los sources correctos (XBRL y calculated).
- El nodo devuelve SOLO la clave "financials" — no el estado completo.
  (Invariante de todos los nodos: devolver solo las claves que modifican.)

Todos los tests mockean EdgarAdapter.fetch_raw — zero llamadas reales a EDGAR.
"""

import json
from pathlib import Path

# Fixture cacheado: respuesta real de EDGAR para AAPL FY2024
# Permite tests reproducibles sin red ni API keys
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json").read_text())


def test_financials_node_returns_report(mocker):
    """El nodo devuelve un FinancialsReport Pydantic válido con ticker correcto."""
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node
    from due_diligence.schemas.base import FinancialsReport

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    assert "financials" in result
    assert isinstance(result["financials"], FinancialsReport)
    assert result["financials"].ticker == "AAPL"


def test_financials_node_preserves_source_xbrl(mocker):
    """Revenue viene de XBRL; FCF viene de 'calculated' (OCF - Capex, no existe en XBRL)."""
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    assert result["financials"].revenue[0].source == "XBRL"
    assert result["financials"].free_cash_flow[0].source == "calculated"


def test_financials_node_only_returns_financials_key(mocker):
    """El nodo devuelve solo sus claves propias — nunca el estado completo."""
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    # Phase 2: el nodo produce "financials" + opcionalmente "historical_cagr_5y" + "historical_cagr_years".
    # No debe incluir ninguna otra clave del estado (preserva el patrón LangGraph).
    allowed_keys = {"financials", "historical_cagr_5y", "historical_cagr_years"}
    assert set(result.keys()).issubset(allowed_keys)
    assert "financials" in result


# ---------------------------------------------------------------------------
# Tests: _compute_revenue_cagr_5y (ADR-019)
# ---------------------------------------------------------------------------

from due_diligence.schemas.base import MetricValue


def test_compute_revenue_cagr_5y_ok():
    """Con 5 periodos (4 años) devuelve (cagr, 4)."""
    from due_diligence.agents.financials import _compute_revenue_cagr_5y

    rev = [
        MetricValue(value=394.3e9, unit="USD", period="FY2025", source="XBRL"),
        MetricValue(value=383.3e9, unit="USD", period="FY2024", source="XBRL"),
        MetricValue(value=394.3e9, unit="USD", period="FY2023", source="XBRL"),
        MetricValue(value=365.8e9, unit="USD", period="FY2022", source="XBRL"),
        MetricValue(value=274.5e9, unit="USD", period="FY2021", source="XBRL"),
    ]
    result = _compute_revenue_cagr_5y(rev)
    assert result is not None
    cagr, years = result
    assert 0.05 < cagr < 0.12
    assert years == 4  # 5 periodos = 4 intervalos de años


def test_compute_revenue_cagr_5y_insufficient_data():
    """Con menos de 2 puntos, devuelve None."""
    from due_diligence.agents.financials import _compute_revenue_cagr_5y

    rev = [MetricValue(value=100.0e9, unit="USD", period="FY2025", source="XBRL")]
    assert _compute_revenue_cagr_5y(rev) is None


def test_compute_revenue_cagr_5y_zero_oldest():
    """Si el valor más antiguo es <= 0, devuelve None."""
    from due_diligence.agents.financials import _compute_revenue_cagr_5y

    rev = [
        MetricValue(value=100.0e9, unit="USD", period="FY2025", source="XBRL"),
        MetricValue(value=0.0, unit="USD", period="FY2024", source="XBRL"),
    ]
    assert _compute_revenue_cagr_5y(rev) is None


def test_compute_revenue_cagr_5y_uses_max_5_years():
    """Con 7 periodos usa una ventana de 5 AÑOS FISCALES reales (T7/EC-83: antes índice 4 = 4 años).

    Decisión de producto de T7: el "CAGR 5y" pasa a ser de 5 años reales (FY2026 vs FY2021).
    """
    from due_diligence.agents.financials import _compute_revenue_cagr_5y

    rev = [
        MetricValue(value=200.0e9, unit="USD", period="FY2026", source="XBRL"),
        MetricValue(value=180.0e9, unit="USD", period="FY2025", source="XBRL"),
        MetricValue(value=160.0e9, unit="USD", period="FY2024", source="XBRL"),
        MetricValue(value=140.0e9, unit="USD", period="FY2023", source="XBRL"),
        MetricValue(value=120.0e9, unit="USD", period="FY2022", source="XBRL"),  # índice 4
        MetricValue(value=100.0e9, unit="USD", period="FY2021", source="XBRL"),
        MetricValue(value=80.0e9, unit="USD", period="FY2020", source="XBRL"),
    ]
    result = _compute_revenue_cagr_5y(rev)
    assert result is not None
    cagr, years = result
    assert years == 5
    assert abs(cagr - ((200.0 / 100.0) ** (1 / 5) - 1)) < 0.001


def test_compute_revenue_cagr_5y_short_series_reports_real_years():
    """Con 4 periodos (FSLR-like), devuelve n_years=3, no 5."""
    from due_diligence.agents.financials import _compute_revenue_cagr_5y

    rev = [
        MetricValue(value=5.2e9, unit="USD", period="FY2025", source="XBRL"),
        MetricValue(value=4.2e9, unit="USD", period="FY2024", source="XBRL"),
        MetricValue(value=3.3e9, unit="USD", period="FY2023", source="XBRL"),
        MetricValue(value=2.9e9, unit="USD", period="FY2022", source="XBRL"),
    ]
    result = _compute_revenue_cagr_5y(rev)
    assert result is not None
    cagr, years = result
    assert years == 3  # 4 periodos = 3 intervalos, NOT 5
    assert cagr > 0.20  # ~21% CAGR para FSLR-like numbers


def test_financials_node_writes_cagr_to_state(mocker):
    """financials_node escribe historical_cagr_5y cuando hay suficientes datos."""
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )
    from due_diligence.agents.financials import financials_node

    result = financials_node({"ticker": "AAPL"})
    # El fixture AAPL tiene revenue series — debe producir CAGR + span real
    assert "historical_cagr_5y" in result
    assert isinstance(result["historical_cagr_5y"], float)
    assert "historical_cagr_years" in result
    assert isinstance(result["historical_cagr_years"], int)
    assert 1 <= result["historical_cagr_years"] <= 4  # fixture AAPL tiene 4 periodos
