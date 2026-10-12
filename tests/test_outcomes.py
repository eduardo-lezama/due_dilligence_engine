"""Tests del informe de resultados de veredictos (A7, MCE-36, ADR-043). Sin red: precios sintéticos."""
import pytest

from due_diligence.evals.outcomes import close_on_or_before, outcome_rows, summarize

_SPY = {"2026-04-17": 500.0, "2026-04-20": 500.0, "2026-04-21": 505.0, "2026-10-09": 550.0}
_CLOSES = {"KO": {"2026-04-17": 60.0, "2026-10-09": 66.0},       # análisis en sábado → viernes 17
           "PLUG": {"2026-04-21": 2.0, "2026-10-09": 1.5}}


def test_close_on_or_before_uses_previous_trading_day():
    assert close_on_or_before(_CLOSES["KO"], "2026-04-18") == ("2026-04-17", 60.0)
    assert close_on_or_before(_CLOSES["KO"], "2026-04-01") is None


def test_outcome_rows_compute_return_and_excess_vs_spy():
    rows = outcome_rows([{"ticker": "KO", "analysis_date": "2026-04-18", "verdict": "HOLD"},
                         {"ticker": "PLUG", "analysis_date": "2026-04-21", "verdict": "SELL",
                          "methodology_version": "H2"}],
                        _CLOSES, _SPY, today="2026-10-11")
    ko, plug = rows
    assert ko.ret == pytest.approx(0.10) and ko.spy_ret == pytest.approx(0.10) and ko.excess == pytest.approx(0.0)
    assert ko.methodology == "pre-H1" and ko.days == 176
    # PLUG −25 % con SPY +8,91 % → exceso −33,91 %
    assert plug.excess == pytest.approx(-0.25 - (550 / 505 - 1), abs=1e-4)


def test_rows_without_prices_are_skipped_not_invented():
    assert outcome_rows([{"ticker": "NOPE", "analysis_date": "2026-04-21"}], _CLOSES, _SPY, "2026-10-11") == []


def test_summarize_groups_by_methodology_and_verdict():
    rows = outcome_rows([{"ticker": "KO", "analysis_date": "2026-04-18", "verdict": "BUY", "methodology_version": "H2"},
                         {"ticker": "PLUG", "analysis_date": "2026-04-21", "verdict": "BUY", "methodology_version": "H2"}],
                        _CLOSES, _SPY, today="2026-10-11")
    (s,) = summarize(rows)
    assert (s["methodology"], s["verdict"], s["n"], s["pct_beat_spy"]) == ("H2", "BUY", 2, 0.0)
    assert s["median_days"] == 174                      # (176 + 173) / 2 → int


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
