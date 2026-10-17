"""Tests del bloque B, B3: competidores sugeridos automáticamente (Finnhub) y su origen en el informe."""
import pytest

from due_diligence.agents.checkpoint import checkpoint1_node
from due_diligence.tools.finnhub import FinnhubAdapter


@pytest.mark.real_finnhub_fetch
def test_fetch_company_peers_drops_the_subject_and_limits(mocker):
    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "k")
    client = mocker.patch("finnhub.Client").return_value
    client.company_peers.return_value = ["KO", "PEP", "kdp", "MNST", "CELH", "COKE", "FIZZ"]
    assert FinnhubAdapter.fetch_company_peers("ko", limit=5) == ["PEP", "KDP", "MNST", "CELH", "COKE"]


@pytest.mark.real_finnhub_fetch
def test_fetch_company_peers_degrades_to_empty_without_key_or_on_error(mocker):
    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "")
    assert FinnhubAdapter.fetch_company_peers("KO") == []
    mocker.patch("due_diligence.tools.finnhub.settings.finnhub_api_key", "k")
    mocker.patch("finnhub.Client").return_value.company_peers.side_effect = RuntimeError("429")
    assert FinnhubAdapter.fetch_company_peers("KO") == []


@pytest.mark.parametrize("resume,expected", [
    ({"proceed": True, "peers": "PEP,KDP", "peers_source": "auto"}, ("auto", ["PEP", "KDP"])),
    ({"proceed": True, "peers": "MNST"}, ("user", ["MNST"])),
    ({"proceed": True, "peers": "", "peers_source": "weird"}, ("user", [])),
])
def test_checkpoint1_records_where_the_peer_list_came_from(mocker, resume, expected):
    mocker.patch("due_diligence.agents.checkpoint.interrupt", return_value=resume)
    out = checkpoint1_node({"ticker": "KO"})
    assert (out["peers_source"], out["peers"]) == expected


def test_relative_section_names_the_source_of_the_peers():
    from due_diligence.agents.report_facts import relative_section
    from due_diligence.schemas.reports import PeerValuation
    state = {"peer_valuations": [PeerValuation(ticker="PEP", roic=0.2, period="FY2025", ev_ebit=18.0)],
             "peers_source": "auto"}
    rows = dict(relative_section(state).rows)
    assert "suggested automatically" in rows["Peers"]
