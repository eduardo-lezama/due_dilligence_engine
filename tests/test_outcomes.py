"""Tests del informe de resultados de veredictos (A7, MCE-36, ADR-043, C4). Sin red: precios sintéticos."""
import pytest

from due_diligence.evals.outcomes import (add_months, bootstrap_median_ci, close_on_or_before,
                                          dedup_quarterly, outcome_rows, quarter_key, summarize,
                                          total_return)

_FLAT = {"2026-01-02": 100.0, "2026-04-02": 100.0, "2026-07-02": 100.0, "2026-10-02": 100.0}
_UP10 = {"2026-01-02": 100.0, "2026-04-02": 105.0, "2026-07-02": 110.0, "2026-10-02": 120.0}
_BENCH = {"SPY": _FLAT, "IWD": _FLAT}


def test_close_on_or_before_uses_previous_trading_day():
    assert close_on_or_before(_UP10, "2026-01-05") == ("2026-01-02", 100.0)
    assert close_on_or_before(_UP10, "2025-12-01") is None


def test_add_months_clamps_end_of_month_and_crosses_year():
    assert add_months("2026-01-31", 1) == "2026-02-28"
    assert add_months("2026-11-15", 3) == "2027-02-15"
    assert quarter_key("2026-04-01") == (2026, 2) and quarter_key("2026-12-31") == (2026, 4)


def test_horizon_selection_pending_until_elapsed():
    (r,) = outcome_rows([{"ticker": "X", "analysis_date": "2026-01-02", "verdict": "BUY"}],
                        {"X": _UP10}, _BENCH, today="2026-07-02", dividends_by_ticker={})
    assert r.horizons["3m"].ret == pytest.approx(0.05)
    assert r.horizons["6m"].ret == pytest.approx(0.10)       # justo hoy: ya transcurrido
    assert r.horizons["12m"] is None                         # pending, no se rellena con "hoy"
    assert r.horizons["to_date"].ret == pytest.approx(0.10)
    assert r.horizons["6m"].excess["SPY"] == pytest.approx(0.10) and r.horizons["6m"].excess["IWD"] == pytest.approx(0.10)


def test_rows_without_prices_are_skipped_not_invented():
    assert outcome_rows([{"ticker": "NOPE", "analysis_date": "2026-01-02"}], {}, _BENCH, "2026-10-02") == []


def test_total_return_reinvests_dividend_on_ex_date():
    closes = {"2026-01-02": 100.0, "2026-03-02": 50.0, "2026-04-02": 50.0}   # cae por el pago: ex-div
    # sin dividendo −50 %; con 5 $ en ex-fecha reinvertidos a 50: factor 0,5·1,1 = 0,55
    assert total_return(closes, [], "2026-01-02", "2026-04-02") == pytest.approx(-0.5)
    assert total_return(closes, [("2026-03-02", 5.0)], "2026-01-02", "2026-04-02") == pytest.approx(-0.45)
    # ex-fecha fuera de (start, end] → ignorada
    assert total_return(closes, [("2025-12-01", 5.0)], "2026-01-02", "2026-04-02") == pytest.approx(-0.5)


def test_dividend_availability_flagged_per_row():
    a = [{"ticker": "X", "analysis_date": "2026-01-02", "verdict": "BUY"}]
    full = {"X": [], "SPY": [], "IWD": []}
    (ok,) = outcome_rows(a, {"X": _UP10}, _BENCH, "2026-10-02", full)
    assert ok.stock_dividends and ok.bench_dividends          # [] = no paga, no "no disponible"
    (po,) = outcome_rows(a, {"X": _UP10}, _BENCH, "2026-10-02", {"X": None, "SPY": [], "IWD": []})
    assert not po.stock_dividends and po.bench_dividends
    assert po.horizons["to_date"].ret == pytest.approx(0.20)      # sigue calculando, pero etiquetado


def test_dedup_quarterly_keeps_latest_per_ticker_quarter():
    out = dedup_quarterly([
        {"ticker": "BBW", "analysis_date": "2026-04-02"}, {"ticker": "BBW", "analysis_date": "2026-05-20"},
        {"ticker": "BBW", "analysis_date": "2026-07-01"}, {"ticker": "KO", "analysis_date": "2026-04-30"}])
    assert [(a["ticker"], a["analysis_date"]) for a in out] == [
        ("KO", "2026-04-30"), ("BBW", "2026-05-20"), ("BBW", "2026-07-01")]


def test_bootstrap_is_deterministic_and_brackets_median():
    vals = [-0.1, 0.0, 0.05, 0.08, 0.2, -0.03, 0.12, 0.01, 0.04, 0.07]
    assert bootstrap_median_ci(vals) == bootstrap_median_ci(vals)
    lo, hi = bootstrap_median_ci(vals)
    assert lo <= 0.045 <= hi and lo < hi
    assert bootstrap_median_ci([]) is None
    assert bootstrap_median_ci([0.1]) == (0.1, 0.1)


def test_summarize_reports_n_pending_and_too_few():
    rows = outcome_rows(
        [{"ticker": "X", "analysis_date": "2026-01-02", "verdict": "BUY", "methodology_version": "H2"},
         {"ticker": "X", "analysis_date": "2026-07-02", "verdict": "BUY", "methodology_version": "H2"}],
        {"X": _UP10}, _BENCH, today="2026-10-02")
    by_h = {s["horizon"]: s for s in summarize(rows)}
    assert by_h["12m"]["n"] == 0 and by_h["12m"]["pending"] == 2 and by_h["12m"]["SPY"] is None
    assert by_h["3m"]["n"] == 2 and by_h["3m"]["too_few"] is True
    assert by_h["3m"]["SPY"]["median_excess"] == pytest.approx(0.0704, abs=1e-3)   # (5 % y 9,09 %) → 7,04 %


def test_polygon_daily_closes_keyed_by_utc_date(monkeypatch):
    import unittest.mock
    from types import SimpleNamespace
    from due_diligence.tools.polygon import PolygonAdapter
    monkeypatch.setattr("due_diligence.tools.polygon.settings.polygon_api_key", "k")
    aggs = [SimpleNamespace(close=60.0, timestamp=1776398400000),   # 2026-04-17 04:00 UTC
            SimpleNamespace(close=None, timestamp=1776657600000)]   # sin cierre → se omite
    client = unittest.mock.MagicMock()
    client.list_aggs.return_value = aggs
    with unittest.mock.patch("polygon.RESTClient", return_value=client):
        out = PolygonAdapter.fetch_daily_closes("KO", "2026-04-01", "2026-10-11")
    assert out == {"2026-04-17": 60.0}
    assert client.list_aggs.call_args.kwargs == {"adjusted": True}


def test_polygon_fetch_dividends_split_adjusts_and_distinguishes_unavailable(monkeypatch):
    """El cliente de Polygon solo da `cash_amount` (sin ajustar): se ajusta con los splits posteriores."""
    import unittest.mock
    from types import SimpleNamespace
    from due_diligence.tools.polygon import PolygonAdapter
    monkeypatch.setattr("due_diligence.tools.polygon.settings.polygon_api_key", "")
    assert PolygonAdapter.fetch_dividends("KO", "2026-01-01", "2026-10-01") is None   # no disponible
    monkeypatch.setattr("due_diligence.tools.polygon.settings.polygon_api_key", "k")
    client = unittest.mock.MagicMock()
    client.list_dividends.return_value = [
        SimpleNamespace(ex_dividend_date="2026-06-13", cash_amount=2.0),
        SimpleNamespace(ex_dividend_date="2026-03-13", cash_amount=0.5),
        SimpleNamespace(ex_dividend_date=None, cash_amount=9.0)]
    client.list_splits.return_value = [SimpleNamespace(execution_date="2026-05-01", split_from=1, split_to=2)]
    with unittest.mock.patch("polygon.RESTClient", return_value=client):
        out = PolygonAdapter.fetch_dividends("KO", "2026-01-01", "2026-10-01")
    assert out == [("2026-03-13", 0.25), ("2026-06-13", 2.0)]     # el de marzo, antes del 2:1, se divide


def test_outcomes_script_keeps_latest_analysis_per_ticker_and_day(tmp_path):
    import sqlite3
    import sys
    sys.path.insert(0, "scripts")
    from outcomes_report import _load_analyses
    db = tmp_path / "a.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY, ticker TEXT, analysis_date TEXT, verdict TEXT)")
        c.executemany("INSERT INTO analyses (ticker, analysis_date, verdict) VALUES (?, ?, ?)",
                      [("AAPL", "2026-04-21", "HOLD"), ("AAPL", "2026-04-21", "SELL"), ("KO", "2026-04-22", "BUY")])
    rows = _load_analyses(str(db), None)
    assert sorted((r["ticker"], r["verdict"]) for r in rows) == [("AAPL", "SELL"), ("KO", "BUY")]


def test_dividends_are_expressed_in_todays_shares_after_later_splits():
    # Revisión del bloque C: los cierres vienen ajustados por splits y los dividendos de Polygon no.
    from due_diligence.tools.polygon import split_adjust_dividends
    divs = [("2024-03-05", 0.16), ("2024-09-12", 0.01)]          # NVDA: 10:1 el 2024-06-10
    assert split_adjust_dividends(divs, [("2024-06-10", 10.0)]) == [("2024-03-05", 0.016), ("2024-09-12", 0.01)]
    assert split_adjust_dividends(divs, []) == divs


def test_stale_last_close_is_not_reused_for_a_later_horizon():
    # Revisión del bloque C: una empresa deslistada no "sigue" con su último precio mientras SPY avanza.
    from due_diligence.evals.outcomes import total_return
    closes = {"2026-01-02": 10.0, "2026-02-02": 11.0}
    assert total_return(closes, [], "2026-01-02", "2026-02-03") == pytest.approx(0.1)
    assert total_return(closes, [], "2026-01-02", "2026-04-02") is None
