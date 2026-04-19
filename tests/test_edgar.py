import json
import pytest
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json"


@pytest.fixture
def aapl_raw():
    return json.loads(FIXTURE_PATH.read_text())


def test_parse_financials_returns_valid_report(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.schemas.base import FinancialsReport

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert isinstance(report, FinancialsReport)


def test_parse_financials_ticker_and_cik(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.ticker == "AAPL"
    assert report.cik == "0000320193"


def test_parse_financials_revenue_values(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert len(report.revenue) >= 2
    assert report.revenue[0].value == pytest.approx(391_035_000_000)
    assert report.revenue[0].period == "FY2024"
    assert report.revenue[0].source == "XBRL"


def test_parse_financials_free_cash_flow_is_computed(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_fcf = 118_254_000_000 - 9_447_000_000
    assert report.free_cash_flow[0].value == pytest.approx(expected_fcf)
    assert report.free_cash_flow[0].source == "calculated"


def test_parse_financials_total_debt_is_sum_of_lt_and_st(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_debt = 85_750_000_000 + 10_912_000_000
    assert report.total_debt.value == pytest.approx(expected_debt)


def test_parse_financials_cash_includes_short_term_investments(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_cash = 29_943_000_000 + 35_228_000_000
    assert report.cash_and_equivalents.value == pytest.approx(expected_cash)


def test_parse_financials_source_filing(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.source_filing == "10-K FY2024"
