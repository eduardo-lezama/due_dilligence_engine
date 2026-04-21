"""
Tests para FinnhubAdapter.fetch_transcripts (ADR-016).

Qué se testea:
    - Devuelve los N trimestres más recientes con campos estructurados.
    - Sin API key devuelve lista vacía (degraded mode).
    - Fallo en transcripts_list devuelve lista vacía sin excepción.
    - Fallo en fetch de un transcript individual lo omite y continúa.

Estrategia de aislamiento:
    finnhub.Client se mockea completamente. Zero llamadas reales a Finnhub.
"""

from unittest.mock import MagicMock

import pytest


def test_fetch_transcripts_returns_quarterly_list(mocker):
    """Con 5 transcripts disponibles, devuelve los 4 más recientes correctamente formateados."""
    from due_diligence.tools.finnhub import FinnhubAdapter

    fake_client = MagicMock()
    fake_client.transcripts_list.return_value = {
        "transcripts": [
            {"id": "AAPL_2025_Q4", "year": 2025, "quarter": 4, "time": "2025-10-30"},
            {"id": "AAPL_2025_Q3", "year": 2025, "quarter": 3, "time": "2025-07-30"},
            {"id": "AAPL_2025_Q2", "year": 2025, "quarter": 2, "time": "2025-05-01"},
            {"id": "AAPL_2025_Q1", "year": 2025, "quarter": 1, "time": "2025-01-30"},
            {"id": "AAPL_2024_Q4", "year": 2024, "quarter": 4, "time": "2024-10-31"},
        ]
    }
    fake_client.transcripts.side_effect = lambda tid: {
        "transcript": [
            {"speaker": "Tim Cook", "speech": ["Strong quarter."]},
            {"speaker": "Q&A Operator", "speech": ["Q&A session."]},
            {"speaker": "Analyst", "speech": ["What about margins?"]},
        ],
        "id": tid,
        "symbol": "AAPL",
    }
    mocker.patch("finnhub.Client", return_value=fake_client)
    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "fake-key")

    out = FinnhubAdapter.fetch_transcripts("AAPL", quarters=4)

    assert len(out) == 4
    assert out[0]["id"] == "AAPL_2025_Q4"
    assert out[0]["year"] == 2025
    assert out[0]["quarter"] == 4
    assert "Strong quarter." in out[0]["prepared_remarks"]
    # Q&A debe estar separado de prepared_remarks
    assert "What about margins?" in out[0]["qa"]


def test_fetch_transcripts_returns_empty_without_api_key(mocker):
    """Sin API key, devuelve lista vacía (degraded mode)."""
    from due_diligence.tools.finnhub import FinnhubAdapter

    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "")

    result = FinnhubAdapter.fetch_transcripts("AAPL")

    assert result == []


def test_fetch_transcripts_handles_list_fetch_failure(mocker):
    """Si transcripts_list falla, devuelve lista vacía sin excepción."""
    from due_diligence.tools.finnhub import FinnhubAdapter

    fake_client = MagicMock()
    fake_client.transcripts_list.side_effect = Exception("network error")
    mocker.patch("finnhub.Client", return_value=fake_client)
    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "fake-key")

    result = FinnhubAdapter.fetch_transcripts("AAPL")

    assert result == []


def test_fetch_transcripts_skips_failed_individual_transcript(mocker):
    """Si un transcript individual falla, se omite y el resto se procesa."""
    from due_diligence.tools.finnhub import FinnhubAdapter

    fake_client = MagicMock()
    fake_client.transcripts_list.return_value = {
        "transcripts": [
            {"id": "AAPL_2025_Q4", "year": 2025, "quarter": 4, "time": "2025-10-30"},
            {"id": "AAPL_2025_Q3", "year": 2025, "quarter": 3, "time": "2025-07-30"},
        ]
    }
    fake_client.transcripts.side_effect = [
        Exception("timeout"),
        {"transcript": [{"speaker": "Tim Cook", "speech": ["Good results."]}],
         "id": "AAPL_2025_Q3", "symbol": "AAPL"},
    ]
    mocker.patch("finnhub.Client", return_value=fake_client)
    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "fake-key")

    result = FinnhubAdapter.fetch_transcripts("AAPL", quarters=2)

    assert len(result) == 1
    assert result[0]["id"] == "AAPL_2025_Q3"
