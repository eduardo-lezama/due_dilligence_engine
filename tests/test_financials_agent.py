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
    """El nodo solo devuelve las claves que le pertenecen — nunca el estado completo."""
    mocker.patch(
        "due_diligence.agents.financials.EdgarAdapter.fetch_raw",
        return_value=FIXTURE,
    )

    from due_diligence.agents.financials import financials_node

    state = {"ticker": "AAPL", "cik": "0000320193", "as_of_date": "2024-09-28", "run_id": "t1"}
    result = financials_node(state)

    # LangGraph hace el merge — devolver el estado completo sobreescribiría trabajo de otros nodos
    assert set(result.keys()) == {"financials"}
