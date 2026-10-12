"""
Resultados reales de los veredictos (bloque A, A7, MCE-36, ADR-043).

Por qué existe: el harness de evals mide si el Expert obedece las reglas; no dice si sus veredictos
aciertan. Esto compara, para cada análisis guardado en SQLite, el precio en la fecha del análisis con
el de hoy, frente a SPY en el mismo periodo, y agrega por veredicto. Funciones puras: los precios
(cierres diarios ajustados por splits, Polygon) los pasa el script `scripts/outcomes_report.py`.

Lectura honesta: con pocos análisis y pocos meses es una señal débil (ruido de mercado); su valor
crece con el tiempo. Los análisis anteriores a H2 usan otra metodología (HOLD por defecto).
"""
from __future__ import annotations

from datetime import date
from statistics import median
from typing import Optional

from pydantic import BaseModel


class OutcomeRow(BaseModel):
    """Resultado de un análisis: rentabilidad propia y frente a SPY desde la fecha del análisis."""
    ticker: str
    analysis_date: str
    verdict: Optional[str]
    methodology: Optional[str]
    price_then: float           # cierre ajustado en (o justo antes de) la fecha del análisis
    price_now: float
    days: int
    ret: float                  # rentabilidad de la acción (sin dividendos)
    spy_ret: float
    excess: float               # ret − spy_ret


def close_on_or_before(closes: dict[str, float], day: str) -> Optional[tuple[str, float]]:
    """Cierre del día `day` o del último día hábil anterior (fines de semana, festivos)."""
    eligible = [d for d in closes if d <= day]
    if not eligible:
        return None
    d = max(eligible)
    return d, closes[d]


def outcome_rows(analyses: list[dict], closes_by_ticker: dict[str, dict[str, float]],
                 spy: dict[str, float], today: str) -> list[OutcomeRow]:
    """Una fila por análisis con precio en ambos extremos; los que no tienen datos se omiten."""
    spy_now = close_on_or_before(spy, today)
    out: list[OutcomeRow] = []
    for a in analyses:
        closes = closes_by_ticker.get(a["ticker"], {})
        then, now = close_on_or_before(closes, a["analysis_date"]), close_on_or_before(closes, today)
        spy_then = close_on_or_before(spy, a["analysis_date"])
        if not (then and now and spy_then and spy_now) or then[1] <= 0 or spy_then[1] <= 0:
            continue
        ret, spy_ret = now[1] / then[1] - 1, spy_now[1] / spy_then[1] - 1
        out.append(OutcomeRow(
            ticker=a["ticker"], analysis_date=a["analysis_date"], verdict=a.get("verdict"),
            methodology=a.get("methodology_version") or "pre-H1",
            price_then=then[1], price_now=now[1],
            days=(date.fromisoformat(today) - date.fromisoformat(a["analysis_date"])).days,
            ret=round(ret, 4), spy_ret=round(spy_ret, 4), excess=round(ret - spy_ret, 4)))
    return out


def summarize(rows: list[OutcomeRow]) -> list[dict]:
    """Por (metodología, veredicto): n, días medianos, mediana de exceso frente a SPY y % que bate a SPY.

    Los días importan: un exceso a 1 día es ruido; a 6–12 meses empieza a ser señal (MCE-36).
    """
    groups: dict[tuple[str, str], list[OutcomeRow]] = {}
    for r in rows:
        groups.setdefault((r.methodology or "pre-H1", r.verdict or "n/a"), []).append(r)
    return [{"methodology": m, "verdict": v, "n": len(rs),
             "median_days": int(median(x.days for x in rs)),
             "median_excess": round(median(x.excess for x in rs), 4),
             "pct_beat_spy": round(sum(x.excess > 0 for x in rs) / len(rs), 2)}
            for (m, v), rs in sorted(groups.items())]
