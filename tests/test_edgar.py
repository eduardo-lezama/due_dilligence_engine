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
    assert report.revenue[0].value == pytest.approx(416_161_000_000)
    assert report.revenue[0].period == "FY2025"
    assert report.revenue[0].source == "XBRL"


def test_parse_financials_free_cash_flow_is_computed(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_fcf = 111_482_000_000 - 12_715_000_000  # OCF - capex FY2025
    assert report.free_cash_flow[0].value == pytest.approx(expected_fcf)
    assert report.free_cash_flow[0].source == "calculated"


def test_parse_financials_total_debt_is_sum_of_lt_and_st(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_debt = 90_678_000_000 + 0  # LT debt FY2025, no ST borrowings
    assert report.total_debt.value == pytest.approx(expected_debt)


def test_parse_financials_cash_includes_short_term_investments(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_cash = 35_934_000_000 + 0  # Cash FY2025, STI=0 in this filing
    assert report.cash_and_equivalents.value == pytest.approx(expected_cash)


def test_parse_financials_source_filing(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.source_filing == "10-K FY2025"


def test_parse_financials_roic_series(aapl_raw):
    """ROIC calculado correctamente desde income statement + balance sheet."""
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)

    assert len(report.roic_series) >= 1
    assert report.roic_series[0].unit == "ratio"
    assert report.roic_series[0].period == "FY2025"
    # FY2025: tax_rate = 20719/132729 ≈ 15.6%, NOPAT = 133050×0.844 ≈ 112245M
    # IC = 73733 + 90678 - 35934 ≈ 128477M → ROIC ≈ 0.874
    assert report.roic_series[0].value == pytest.approx(0.874, abs=0.01)


def test_parse_financials_invested_capital(aapl_raw):
    """Invested Capital primaria: Equity + Debt - Cash (cuando no es degenerada)."""
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)

    assert report.invested_capital is not None
    # Para AAPL: ic_primary ($128B) > 10% × ic_fallback ($33B) → primary se usa
    expected_ic = 73_733_000_000 + 90_678_000_000 - 35_934_000_000
    assert report.invested_capital.value == pytest.approx(expected_ic)
    assert report.invested_capital.source == "equity+debt-cash"
    assert report.stockholders_equity.value == pytest.approx(73_733_000_000)


def test_parse_financials_invested_capital_fallback_for_buyback_heavy(aapl_raw):
    """
    EC-18 / ADR-023: cuando la fórmula primaria (Equity+Debt-Cash) es degenerada
    por buybacks agresivos, el sistema usa total_assets-cash como fallback.

    Escenario simulado: mutamos el equity a un valor pequeño para reproducir
    una empresa con buybacks acumulados (BBW, Domino's, AutoZone). Debe usar
    el fallback en lugar de excluir el ROIC.
    """
    from due_diligence.tools.edgar import EdgarAdapter

    # Mutate the raw fixture: equity negativo (típico de empresas con buybacks acumulados
    # superiores al equity contribuido + retained earnings — caso AutoZone, Domino's).
    raw_modified = json.loads(json.dumps(aapl_raw))  # deep copy
    raw_modified["balance_sheet"]["stockholders_equity"]["value"] = -50_000_000_000  # $-50B
    # ic_primary = -50B + 90B - 36B = $4B
    # ic_fallback = total_assets - cash ≈ $328B
    # threshold = 0.10 × 328B = $33B
    # primary ($4B) < threshold → fallback se usa

    report = EdgarAdapter.parse_financials(raw_modified)

    assert report.invested_capital is not None
    assert "fallback" in report.invested_capital.source
    # ROIC debe seguir calculándose con el fallback
    assert len(report.roic_series) >= 1
    assert report.roic_series[0].value > 0  # ROIC válido, no excluido
