"""
Informe de resultados reales de los veredictos (bloque A, A7, MCE-36, ADR-043).

Lee los análisis de SQLite (solo lectura), pide a Polygon los cierres diarios ajustados de cada
ticker y de SPY, y escribe una tabla por análisis y un resumen por (metodología, veredicto) en
`data/evals/outcomes/<hoy>.md`. Coste de LLM: 0 $. Polygon gratuito: 5 llamadas/min → pausa entre
tickers (una llamada por ticker).

Uso:
    uv run python scripts/outcomes_report.py            # todos los análisis
    uv run python scripts/outcomes_report.py --since 2026-10-01
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from due_diligence.config import settings
from due_diligence.evals.outcomes import outcome_rows, summarize
from due_diligence.tools.polygon import PolygonAdapter

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "evals" / "outcomes"
POLYGON_PAUSE_S = 13   # 5 llamadas/min en el plan gratuito


def _load_analyses(db_path: str, since: str | None) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analyses)")}
        method = "methodology_version" if "methodology_version" in cols else "NULL AS methodology_version"
        # Un relanzamiento del mismo ticker el mismo día duplicaba la fila: se queda el ÚLTIMO (mayor id).
        sql = (f"SELECT ticker, analysis_date, verdict, {method} FROM analyses WHERE id IN "
               f"(SELECT MAX(id) FROM analyses GROUP BY ticker, analysis_date)")
        rows = [dict(r) for r in conn.execute(sql + (" AND analysis_date >= ?" if since else ""),
                                              (since,) if since else ())]
    return [r for r in rows if r["analysis_date"]]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--since", help="solo análisis desde esta fecha (YYYY-MM-DD)")
    args = ap.parse_args(argv)
    analyses = _load_analyses(settings.db_path, args.since)
    if not analyses:
        print("No analyses in the database.")
        return 0
    today = date.today().isoformat()
    start = (date.fromisoformat(min(a["analysis_date"] for a in analyses)) - timedelta(days=7)).isoformat()
    tickers = sorted({a["ticker"] for a in analyses})
    closes: dict[str, dict[str, float]] = {}
    for i, t in enumerate(["SPY", *tickers]):
        if i:
            time.sleep(POLYGON_PAUSE_S)
        try:
            closes[t] = PolygonAdapter.fetch_daily_closes(t, start, today)
        except Exception as e:  # un ticker deslistado no debe tumbar el informe: se dice y se sigue
            print(f"{t}: prices unavailable ({type(e).__name__}: {e})", flush=True)
            closes[t] = {}
        print(f"{t}: {len(closes[t])} daily closes", flush=True)

    rows = outcome_rows(analyses, closes, closes.get("SPY", {}), today)
    skipped = len(analyses) - len(rows)
    lines = [f"# Verdict outcomes — {today}", "",
             f"{len(rows)} analyses with prices ({skipped} skipped: no price data). Returns use "
             "split-adjusted closes, exclude dividends, and are measured against SPY over the same days.",
             "Pre-H1/H1 analyses used an older methodology (HOLD by default): read them separately.", "",
             "## Summary by methodology and verdict", "",
             "| Methodology | Verdict | n | Median days | Median excess vs SPY | % beating SPY |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {s['methodology']} | {s['verdict']} | {s['n']} | {s['median_days']} | "
              f"{s['median_excess']:+.1%} | {s['pct_beat_spy']:.0%} |" for s in summarize(rows)]
    lines += ["", "## Per analysis", "",
              "| Ticker | Date | Verdict | Methodology | Days | Return | SPY | Excess |",
              "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r.ticker} | {r.analysis_date} | {r.verdict} | {r.methodology} | {r.days} | "
              f"{r.ret:+.1%} | {r.spy_ret:+.1%} | {r.excess:+.1%} |"
              for r in sorted(rows, key=lambda r: (r.analysis_date, r.ticker))]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{today}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
