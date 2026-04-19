"""
Tests para el Supervisor node.

Verifican:
- Resolución de ticker → CIK (formato 10 dígitos con ceros a la izquierda).
- Extracción de as_of_date del 10-K más reciente.
- Generación de run_id único.
- Normalización del ticker a mayúsculas.
- Error explícito cuando el ticker no existe en EDGAR (fail loud, ADR-005).

Todos los tests mockean edgartools.Company — zero llamadas reales a EDGAR.
"""

import pytest


def test_supervisor_resolves_cik(mocker):
    """Resuelve ticker a CIK con formato 10 dígitos (zfill)."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193                          # entero — como lo devuelve edgartools
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "AAPL"})

    assert result["cik"] == "0000320193"               # zfill(10): 320193 → "0000320193"
    assert result["as_of_date"] == "2024-09-28"
    assert "run_id" in result
    assert len(result["run_id"]) > 0


def test_supervisor_uppercases_ticker(mocker):
    """Ticker en minúsculas se normaliza a mayúsculas antes de llamar a EDGAR."""
    mock_company = mocker.MagicMock()
    mock_company.cik = 320193
    mock_latest = mocker.MagicMock()
    mock_latest.period_of_report = "2024-09-28"
    mock_company.get_filings.return_value.latest.return_value = mock_latest
    mocker.patch("due_diligence.agents.supervisor.Company", return_value=mock_company)

    from due_diligence.agents.supervisor import supervisor_node

    result = supervisor_node({"ticker": "aapl"})
    assert result["cik"] == "0000320193"


def test_supervisor_invalid_ticker_raises(mocker):
    """Ticker inexistente en EDGAR → ValueError con el ticker en el mensaje (fail loud)."""
    mocker.patch(
        "due_diligence.agents.supervisor.Company",
        side_effect=Exception("company not found"),
    )

    from due_diligence.agents.supervisor import supervisor_node

    with pytest.raises(ValueError, match="INVALID_TICKER"):
        supervisor_node({"ticker": "INVALID_TICKER"})
