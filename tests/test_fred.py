"""Tests del adaptador FRED y del tipo sin riesgo vivo en el WACC (bloque A, A5, ADR-043)."""
import unittest.mock

import pytest

from due_diligence.agents.market import fetch_risk_free_rate
from due_diligence.agents.valuation import suggest_wacc
from due_diligence.schemas.base import FinancialsReport, MetricValue
from due_diligence.tools.fred import FredAdapter
from due_diligence.tools.xbrl_extract import DataUnavailableError

# Forma real del CSV (verificada en vivo el 2026-10-11): cabecera + "fecha,valor"; "." en festivos.
_CSV = "observation_date,DGS10\n2026-10-06,5.27\n2026-10-07,5.28\n2026-10-08,5.22\n2026-10-09,.\n"


def test_parse_latest_rate_skips_holidays():
    out = FredAdapter.parse_latest_rate({"series_id": "DGS10", "csv": _CSV})
    assert out == {"value_pct": 5.22, "date": "2026-10-08", "series_id": "DGS10"}


def test_parse_latest_rate_without_numeric_values_fails_loud():
    with pytest.raises(DataUnavailableError, match="DGS10"):
        FredAdapter.parse_latest_rate({"series_id": "DGS10", "csv": "observation_date,DGS10\n2026-10-09,.\n"})


@pytest.mark.real_fred_fetch
def test_fetch_raw_reads_csv_with_user_agent():
    resp = unittest.mock.MagicMock()
    resp.__enter__.return_value.read.return_value = _CSV.encode()
    with unittest.mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
        raw = FredAdapter.fetch_raw("DGS10")
    req = urlopen.call_args.args[0]
    assert "id=DGS10" in req.full_url and req.get_header("User-agent") == "due-diligence-system"
    assert "&cosd=" in req.full_url        # solo la ventana reciente, no la serie desde 1962
    assert raw == {"series_id": "DGS10", "csv": _CSV}


@pytest.mark.real_fred_fetch
def test_fetch_risk_free_rate_builds_metric_value():
    with unittest.mock.patch.object(FredAdapter, "fetch_raw", return_value={"series_id": "DGS10", "csv": _CSV}):
        mv = fetch_risk_free_rate()
    assert mv == MetricValue(value=5.22, unit="%", period="2026-10-08", source="FRED DGS10")


def test_fetch_risk_free_rate_returns_none_when_fred_unavailable():
    """El guard de conftest bloquea FRED: sin dato → None (el WACC usa el estático y lo dice)."""
    assert fetch_risk_free_rate() is None


def _fin() -> FinancialsReport:
    mv = lambda v, p="FY2025": MetricValue(value=v, unit="USD", period=p, source="XBRL")  # noqa: E731
    return FinancialsReport(ticker="T", cik="1", as_of_date="2025-12-31", source_filing="10-K FY2025",
                            revenue=[mv(1000.0)], gross_profit=[mv(400.0)], operating_income=[mv(200.0)],
                            net_income=[mv(150.0)], operating_cash_flow=[mv(180.0)], capex=[mv(30.0)],
                            free_cash_flow=[mv(150.0)], interest_expense=[mv(10.0)],
                            total_assets=mv(2000.0), total_debt=mv(0.0), cash_and_equivalents=mv(50.0))


def test_suggest_wacc_uses_live_risk_free_rate_and_labels_it():
    rf = MetricValue(value=5.22, unit="%", period="2026-10-08", source="FRED DGS10")
    w = suggest_wacc(_fin(), 1e11, rf=rf)
    assert w.cost_of_equity == pytest.approx(0.102)           # 5,22 % + 1,0 × 5,0 % (Ke se redondea a 0,1 pp)
    assert "FRED DGS10 2026-10-08" in w.cost_of_equity_source


def test_suggest_wacc_without_live_rate_uses_static_default_and_says_so():
    w = suggest_wacc(_fin(), 1e11)
    assert w.cost_of_equity == pytest.approx(0.092)            # 4,2 % + 5,0 %
    assert "static default" in w.cost_of_equity_source
