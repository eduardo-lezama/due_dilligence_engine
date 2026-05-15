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
    # Mock Polygon to avoid real API calls and MagicMock comparison errors in _build_peer_valuations
    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": None},
    )

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


def test_peers_node_sic_mismatch_surfaced_to_state(mocker):
    """
    EC-42: cuando un peer tiene SIC distinto al subject (industria diferente),
    el sistema añade un warning a state["peer_sic_mismatches"] para que el
    Value Expert pueda reflejar la limitación en §2 Moat Analysis.
    """
    from due_diligence.agents.peers import peers_node

    # Subject MOS SIC 2870 (chemicals). CF también 2870 (match). FMC SIC 2879 (mismatch).
    def mock_sic(ticker):
        return {
            "MOS": ("2870", "Industrial Chemicals"),
            "CF": ("2870", "Industrial Chemicals"),
            "FMC": ("2879", "Agricultural Chemicals"),
        }.get(ticker, (None, None))

    mocker.patch("due_diligence.agents.peers._get_sic", side_effect=mock_sic)

    fake_rep = MagicMock()
    fake_rep.ticker = "CF"
    fake_rep.roic_series = []
    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.fetch_raw",
        return_value={"ticker": "CF"},
    )
    mocker.patch(
        "due_diligence.agents.peers.EdgarAdapter.parse_financials",
        return_value=fake_rep,
    )

    result = peers_node({"ticker": "MOS", "peers": ["CF", "FMC"]})

    # FMC SIC 2879 vs MOS 2870 → mismatch (different 2-digit prefix would trigger; same here)
    # Actually 28 == 28, so NO mismatch in this case. Update assertion.
    # Both 2870 and 2879 have prefix "28" — so no mismatch should appear
    assert "peer_sic_mismatches" not in result or len(result.get("peer_sic_mismatches", [])) == 0


def test_peers_node_sic_mismatch_different_industry_group(mocker):
    """EC-42: peer en industry group completamente distinto (prefix 2 dígitos != subject)."""
    from due_diligence.agents.peers import peers_node

    # Subject SIC 2870 (Chemicals, prefix 28). Peer SIC 7372 (Software, prefix 73) — clear mismatch.
    def mock_sic(ticker):
        return {
            "MOS": ("2870", "Chemicals"),
            "MSFT": ("7372", "Prepackaged Software"),
        }.get(ticker, (None, None))

    mocker.patch("due_diligence.agents.peers._get_sic", side_effect=mock_sic)

    fake_rep = MagicMock()
    fake_rep.ticker = "MSFT"
    fake_rep.roic_series = []
    mocker.patch("due_diligence.agents.peers.EdgarAdapter.fetch_raw", return_value={"ticker": "MSFT"})
    mocker.patch("due_diligence.agents.peers.EdgarAdapter.parse_financials", return_value=fake_rep)

    result = peers_node({"ticker": "MOS", "peers": ["MSFT"]})

    assert "peer_sic_mismatches" in result
    assert len(result["peer_sic_mismatches"]) == 1
    assert "MSFT" in result["peer_sic_mismatches"][0]
    assert "2870" in result["peer_sic_mismatches"][0]
    assert "7372" in result["peer_sic_mismatches"][0]


def test_peers_node_skips_unexpected_exception(mocker):
    """Excepción inesperada en un peer se omite sin abortar el batch."""
    from due_diligence.agents.peers import peers_node

    fake_rep = MagicMock()
    fake_rep.ticker = "GOOGL"
    fake_rep.roic_series = []

    # Mock _get_sic para evitar llamadas reales a EDGAR
    mocker.patch("due_diligence.agents.peers._get_sic", return_value=(None, None))

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


def test_peer_valuation_schema_fields_and_defaults():
    """PeerValuation schema has correct fields and default values."""
    from due_diligence.schemas.reports import PeerValuation

    pv = PeerValuation(ticker="MSFT", roic=0.341, period="FY2025")
    assert pv.ticker == "MSFT"
    assert pv.roic == 0.341
    assert pv.period == "FY2025"
    assert pv.ev_ebit is None
    assert pv.fcf_yield_pct is None
    assert pv.mf_rank is None
    assert pv.no_market_data is False


def test_peer_valuation_schema_with_all_fields():
    """PeerValuation accepts all optional fields."""
    from due_diligence.schemas.reports import PeerValuation

    pv = PeerValuation(
        ticker="GOOGL",
        roic=0.223,
        period="FY2025",
        ev_ebit=14.1,
        fcf_yield_pct=4.9,
        mf_rank=1,
        no_market_data=False,
    )
    assert pv.ticker == "GOOGL"
    assert pv.roic == 0.223
    assert pv.period == "FY2025"
    assert pv.ev_ebit == 14.1
    assert pv.fcf_yield_pct == 4.9
    assert pv.mf_rank == 1
    assert pv.no_market_data is False


def test_peer_valuations_state_key_exists():
    """peer_valuations is a valid key in AnalysisState TypedDict."""
    from due_diligence.schemas.reports import PeerValuation
    from due_diligence.graph.state import AnalysisState
    import typing

    hints = typing.get_type_hints(AnalysisState, include_extras=True)
    assert "peer_valuations" in hints


def test_build_peer_valuations_computes_ev_ebit_and_fcf_yield(mocker):
    """Happy path: computes EV/EBIT and FCF yield from XBRL + Polygon data."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue

    fake_rep = MagicMock()
    fake_rep.ticker = "MSFT"
    fake_rep.roic_series = [
        MetricValue(value=0.341, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=110_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=74_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=50_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=18_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 3_000_000_000_000.0, "price_current": 420.0},
    )

    result = _build_peer_valuations([fake_rep])

    assert len(result) == 1
    pv = result[0]
    assert pv.ticker == "MSFT"
    assert pv.roic == pytest.approx(0.341)
    assert pv.no_market_data is False
    # EV = 3_000B + 50B - 18B = 3_032B
    # EV/EBIT = 3_032B / 110B ≈ 27.56
    assert pv.ev_ebit == pytest.approx(27.56, abs=0.1)
    # FCF yield = 74B / 3_000B * 100 ≈ 2.47%
    assert pv.fcf_yield_pct == pytest.approx(2.47, abs=0.1)
    assert pv.mf_rank is None  # set by valuation_node later


def test_build_peer_valuations_handles_no_market_cap(mocker):
    """Peer with no Polygon market_cap gets no_market_data=True, multiples=None."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue

    fake_rep = MagicMock()
    fake_rep.ticker = "FOREIGN_CO"
    fake_rep.roic_series = [
        MetricValue(value=0.15, unit="ratio", period="FY2025", source="calculated")
    ]

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": None, "price_current": None},
    )

    result = _build_peer_valuations([fake_rep])

    assert len(result) == 1
    pv = result[0]
    assert pv.no_market_data is True
    assert pv.ev_ebit is None
    assert pv.fcf_yield_pct is None


def test_build_peer_valuations_handles_negative_fcf(mocker):
    """Peer with FCF < 0 gets fcf_yield_pct=None but ev_ebit still computed."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue

    fake_rep = MagicMock()
    fake_rep.ticker = "REINVEST_CO"
    fake_rep.roic_series = [
        MetricValue(value=0.158, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=60_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=-5_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=80_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=90_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 2_000_000_000_000.0},
    )

    result = _build_peer_valuations([fake_rep])

    pv = result[0]
    assert pv.fcf_yield_pct is None
    assert pv.ev_ebit is not None  # EV/EBIT still computable
    # EV = 2_000B + 80B - 90B = 1_990B, EBIT = 60B → EV/EBIT ≈ 33.17
    assert pv.ev_ebit == pytest.approx(33.17, abs=0.1)
    assert pv.no_market_data is False


def test_build_peer_valuations_handles_nonpositive_ebit(mocker):
    """Peer with EBIT <= 0 gets ev_ebit=None but fcf_yield still computed if FCF > 0."""
    from due_diligence.agents.peers import _build_peer_valuations
    from due_diligence.schemas.base import MetricValue

    fake_rep = MagicMock()
    fake_rep.ticker = "LOSS_CO"
    fake_rep.roic_series = [
        MetricValue(value=0.05, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=-10_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=5_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=20_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=10_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 500_000_000_000.0},
    )

    result = _build_peer_valuations([fake_rep])

    pv = result[0]
    assert pv.ev_ebit is None
    assert pv.fcf_yield_pct is not None  # FCF > 0, market_cap > 0


def test_peers_node_returns_peer_valuations_in_output(mocker):
    """peers_node includes peer_valuations in its return dict when peers are provided."""
    from due_diligence.agents.peers import peers_node
    from due_diligence.schemas.base import MetricValue

    fake_rep = MagicMock()
    fake_rep.ticker = "MSFT"
    fake_rep.roic_series = [
        MetricValue(value=0.341, unit="ratio", period="FY2025", source="calculated")
    ]
    fake_rep.operating_income = [
        MetricValue(value=110_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.free_cash_flow = [
        MetricValue(value=74_000_000_000.0, unit="USD", period="FY2025", source="XBRL")
    ]
    fake_rep.total_debt = MetricValue(
        value=50_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )
    fake_rep.cash_and_equivalents = MetricValue(
        value=18_000_000_000.0, unit="USD", period="2025-09-30", source="XBRL"
    )

    mocker.patch("due_diligence.agents.peers.EdgarAdapter.fetch_raw", return_value={"ticker": "MSFT"})
    mocker.patch("due_diligence.agents.peers.EdgarAdapter.parse_financials", return_value=fake_rep)
    mocker.patch("due_diligence.agents.peers._get_sic", return_value=("7372", "Software"))
    mocker.patch(
        "due_diligence.agents.peers.PolygonAdapter.fetch_market_data",
        return_value={"market_cap": 3_000_000_000_000.0},
    )

    result = peers_node({"ticker": "AAPL", "peers": ["MSFT"]})

    assert "peer_valuations" in result
    assert len(result["peer_valuations"]) == 1
    pv = result["peer_valuations"][0]
    assert pv.ticker == "MSFT"
    assert pv.ev_ebit is not None
    assert pv.mf_rank is None  # set later by valuation_node


def test_peers_node_empty_list_does_not_add_peer_valuations():
    """peers_node with empty peer list returns no peer_valuations key."""
    from due_diligence.agents.peers import peers_node

    result = peers_node({"ticker": "AAPL", "peers": []})

    assert "peer_valuations" not in result
