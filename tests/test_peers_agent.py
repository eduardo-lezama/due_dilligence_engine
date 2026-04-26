"""
Tests para peers_node (ADR-015).

Qué se testea:
    - El nodo fetcha FinancialsReport para cada peer de state["peers"].
    - Con lista vacía devuelve {"peer_financials": []} sin llamar a EDGAR.
    - Un peer que falla (DataUnavailableError) se omite; el resto continúa.

Estrategia de aislamiento:
    EdgarAdapter.fetch_raw y EdgarAdapter.parse_financials se mockean.
    Zero llamadas reales a EDGAR.
"""

from unittest.mock import MagicMock

import pytest


def test_peers_node_fetches_financials_for_each_peer(mocker):
    """Con dos peers, devuelve dos FinancialsReport."""
    from due_diligence.agents.peers import peers_node

    fake_rep_msft = MagicMock()
    fake_rep_msft.ticker = "MSFT"
    fake_rep_msft.roic_series = []  # sin ROIC — evita construcción de PeerROIC con MagicMock
    fake_rep_googl = MagicMock()
    fake_rep_googl.ticker = "GOOGL"
    fake_rep_googl.roic_series = []

    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.fetch_raw",
        side_effect=[{"ticker": "MSFT"}, {"ticker": "GOOGL"}],
    )
    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.parse_financials",
        side_effect=[fake_rep_msft, fake_rep_googl],
    )

    result = peers_node({"ticker": "AAPL", "peers": ["MSFT", "GOOGL"]})

    assert "peer_financials" in result
    assert len(result["peer_financials"]) == 2
    assert result["peer_financials"][0].ticker == "MSFT"
    assert result["peer_financials"][1].ticker == "GOOGL"


def test_peers_node_handles_empty_peer_list():
    """Sin peers, devuelve lista vacía sin llamar a EDGAR."""
    from due_diligence.agents.peers import peers_node

    result = peers_node({"ticker": "AAPL", "peers": []})

    assert result == {"peer_financials": []}


def test_peers_node_handles_missing_peers_key():
    """Si 'peers' no está en el estado, equivale a lista vacía."""
    from due_diligence.agents.peers import peers_node

    result = peers_node({"ticker": "AAPL"})

    assert result == {"peer_financials": []}


def test_peers_node_logs_and_skips_failing_peer(mocker):
    """Un peer que lanza DataUnavailableError se omite; el resto se procesa."""
    from due_diligence.agents.peers import peers_node
    from due_diligence.tools.edgar import DataUnavailableError

    fake_rep = MagicMock()
    fake_rep.ticker = "MSFT"
    fake_rep.roic_series = []

    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.fetch_raw",
        side_effect=[DataUnavailableError("BADTICKER: not found"), {"ticker": "MSFT"}],
    )
    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.parse_financials",
        return_value=fake_rep,
    )

    result = peers_node({"ticker": "AAPL", "peers": ["BADTICKER", "MSFT"]})

    assert len(result["peer_financials"]) == 1
    assert result["peer_financials"][0].ticker == "MSFT"


def test_peers_node_injects_peer_roic_into_competitive(mocker):
    """peers_node actualiza competitive con ROIC y trail histórico."""
    from due_diligence.agents.peers import peers_node
    from due_diligence.schemas.reports import CompetitiveReport
    from due_diligence.schemas.base import MetricValue

    fake_rep = MagicMock()
    fake_rep.ticker = "LRCX"
    fake_rep.roic_series = [
        MetricValue(value=0.28, unit="ratio", period="FY2025", source="calculated"),
        MetricValue(value=0.25, unit="ratio", period="FY2024", source="calculated"),
        MetricValue(value=0.22, unit="ratio", period="FY2023", source="calculated"),
    ]

    mocker.patch("due_diligence.agents.peers.EdgarAdapter.fetch_raw", return_value={"ticker": "LRCX"})
    mocker.patch("due_diligence.agents.peers.EdgarAdapter.parse_financials", return_value=fake_rep)

    competitive = CompetitiveReport(ticker="ACMR", stub=False, moat_classification="narrow")
    state = {"ticker": "ACMR", "peers": ["LRCX"], "competitive": competitive}

    result = peers_node(state)

    assert "competitive" in result
    pr = result["competitive"].peer_roic_comparison[0]
    assert pr.ticker == "LRCX"
    assert abs(pr.roic - 0.28) < 0.001          # most recent
    assert pr.period == "FY2025"
    assert len(pr.roic_trail) == 3               # trail captured
    assert pr.roic_trail[0]["period"] == "FY2025"
    assert pr.roic_trail[2]["period"] == "FY2023"


def test_peers_node_skips_unexpected_exception(mocker):
    """Excepción inesperada en un peer se omite sin abortar el batch."""
    from due_diligence.agents.peers import peers_node

    fake_rep = MagicMock()
    fake_rep.ticker = "GOOGL"
    fake_rep.roic_series = []

    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.fetch_raw",
        side_effect=[RuntimeError("network timeout"), {"ticker": "GOOGL"}],
    )
    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.parse_financials",
        return_value=fake_rep,
    )

    result = peers_node({"ticker": "AAPL", "peers": ["BADPEER", "GOOGL"]})

    assert len(result["peer_financials"]) == 1
