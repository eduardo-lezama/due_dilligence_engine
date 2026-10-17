"""
Informe de resultados reales de los veredictos (bloque A, A7, MCE-36, ADR-043).

Lee los análisis de SQLite (solo lectura), pide a Polygon los cierres diarios ajustados de cada
ticker, de SPY y de IWD, más sus dividendos, y escribe un resumen por (metodología, veredicto,
horizonte 3/6/12 meses) con IC bootstrap y una tabla por análisis en `data/evals/outcomes/<hoy>.md`.
Coste de LLM: 0 $. Polygon gratuito: 5 llamadas/min → pausa entre llamadas (dos por ticker: cierres
y dividendos).

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
from due_diligence.evals.outcomes import (BENCHMARKS, HORIZONS, METHODOLOGY_NOTE, dedup_quarterly,
                                          outcome_rows, summarize)
from due_diligence.tools.polygon import PolygonAdapter

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "evals" / "outcomes"
POLYGON_PAUSE_S = 13   # 5 llamadas/min en el plan gratuito


def _with_retry(fn, *args, tries: int = 3):
    """Reintenta tras un 429 (límite del plan gratuito, a veces compartido con otros procesos): espera 65 s."""
    for attempt in range(tries):
        try:
            return fn(*args)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(65)


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
    n_all = len(analyses)
    analyses = dedup_quarterly(analyses)        # un análisis por ticker y trimestre (el último)
    start = (date.fromisoformat(min(a["analysis_date"] for a in analyses)) - timedelta(days=7)).isoformat()
    tickers = sorted({a["ticker"] for a in analyses})
    closes: dict[str, dict[str, float]] = {}
    divs: dict[str, list[tuple[str, float]] | None] = {}
    for i, t in enumerate([*BENCHMARKS, *tickers]):
        if i:
            time.sleep(POLYGON_PAUSE_S)
        try:
            closes[t] = _with_retry(PolygonAdapter.fetch_daily_closes, t, start, today)
        except Exception as e:  # un ticker deslistado no debe tumbar el informe: se dice y se sigue
            print(f"{t}: prices unavailable ({type(e).__name__}: {e})", flush=True)
            closes[t] = {}
        time.sleep(POLYGON_PAUSE_S)             # segunda llamada del ticker (dividendos)
        try:
            divs[t] = _with_retry(PolygonAdapter.fetch_dividends, t, start, today)
        except Exception as e:  # sin dividendos fiables: None → la fila lo marca, no se asume 0
            print(f"{t}: dividends unavailable ({type(e).__name__}: {e})", flush=True)
            divs[t] = None
        print(f"{t}: {len(closes[t])} daily closes, "
              f"{'n/a' if divs[t] is None else len(divs[t])} dividends", flush=True)

    rows = outcome_rows(analyses, closes, {b: closes.get(b, {}) for b in BENCHMARKS}, today, divs)
    skipped = len(analyses) - len(rows)
    lines = [f"# Verdict outcomes — {today}", "", METHODOLOGY_NOTE, "",
             f"{n_all} stored analyses → {len(analyses)} after keeping one per ticker per quarter → "
             f"{len(rows)} with prices ({skipped} skipped: no price data). "
             "Pre-H1/H1 analyses used an older methodology (HOLD by default): read them separately.", "",
             "## Summary by methodology, verdict and horizon", "",
             "Median excess total return [90% bootstrap CI]; pending = horizon not yet elapsed.", "",
             "| Methodology | Verdict | Horizon | n | Pending | Excess vs SPY | Excess vs IWD | % beat IWD | Note |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summarize(rows):
        def cell(b: str) -> str:
            c = s[b]
            return "pending" if c is None else f"{c['median_excess']:+.1%} [{c['ci'][0]:+.1%}, {c['ci'][1]:+.1%}]"
        pct = "-" if s["IWD"] is None else f"{s['IWD']['pct_beat']:.0%}"
        note = "too few to conclude" if s["n"] and s["too_few"] else ("" if s["n"] else "pending")
        lines.append(f"| {s['methodology']} | {s['verdict']} | {s['horizon']} | {s['n']} | {s['pending']} | "
                     f"{cell('SPY')} | {cell('IWD')} | {pct} | {note} |")
    lines += ["", "## Per analysis", "",
              "Total return of the stock, then excess vs SPY / IWD. 'price-only' = dividends unavailable "
              "for the stock or a benchmark (no silent fallback).", "",
              "| Ticker | Date | Verdict | Methodology | Days | Dividends | " +
              " | ".join(f"{h} ret / xSPY / xIWD" for h in HORIZONS) + " |",
              "|---|---|---|---|---|---|" + "---|" * len(HORIZONS)]
    for r in sorted(rows, key=lambda r: (r.analysis_date, r.ticker)):
        div = "ok" if r.stock_dividends and r.bench_dividends else "price-only"
        cells = []
        for h in HORIZONS:
            x = r.horizons[h]
            cells.append("pending" if x is None else
                         f"{x.ret:+.1%} / {x.excess['SPY']:+.1%} / {x.excess['IWD']:+.1%}")
        lines.append(f"| {r.ticker} | {r.analysis_date} | {r.verdict} | {r.methodology} | {r.days} | "
                     f"{div} | " + " | ".join(cells) + " |")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{today}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
