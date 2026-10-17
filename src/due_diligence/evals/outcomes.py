"""
Resultados reales de los veredictos (bloque A, A7, MCE-36, ADR-043; revisión de medición C4).

Por qué existe: el harness de evals mide si el Expert obedece las reglas; no dice si sus veredictos
aciertan. Esto compara, para cada análisis guardado en SQLite, la rentabilidad TOTAL (precio +
dividendos) a horizontes fijos de 3, 6 y 12 meses (y "hasta hoy", solo por transparencia) frente a
SPY y a IWD (Russell 1000 Value), y agrega por (metodología, veredicto) con un intervalo de confianza
bootstrap de la mediana del exceso. Funciones puras: precios y dividendos los pasa el script
`scripts/outcomes_report.py`.

Elecciones de medición (documentadas porque cambian los números):
- Dividendos REINVERTIDOS en la fecha ex-dividendo al cierre de ese día: el factor del periodo es
  Π(1 + div/cierre_ex) · P_fin/P_ini. Es la convención de "total return" estándar y no necesita
  suponer qué se hace con el efectivo. Los cierres vienen ajustados por splits.
- Un análisis por ticker y trimestre natural (el último): repeticiones solapadas no son
  observaciones independientes.
- Un horizonte aún no transcurrido es "pending", nunca se rellena con el precio de hoy.

Lectura honesta: es un registro de auditoría prospectivo, no un test de habilidad. Con n≈50 no puede
detectar una ventaja de 5 %/año (ver `METHODOLOGY_NOTE`).
"""
from __future__ import annotations

import calendar
import random
from datetime import date
from statistics import median
from typing import Optional

from pydantic import BaseModel

HORIZON_MONTHS: dict[str, int] = {"3m": 3, "6m": 6, "12m": 12}   # + "to_date" aparte
HORIZONS: tuple[str, ...] = ("3m", "6m", "12m", "to_date")
BENCHMARKS: tuple[str, ...] = ("SPY", "IWD")
MIN_N = 10                    # por debajo, el informe dice "demasiado pocos para concluir"
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261017     # fijo → el informe es reproducible

# Párrafo de metodología que abre el markdown generado.
METHODOLOGY_NOTE = (
    "**What these numbers can and cannot say.** Each verdict is followed from the analysis date and "
    "measured at fixed horizons (3, 6, 12 months; 'to date' is shown only for transparency) as total "
    "return (price + dividends reinvested on the ex-date) against SPY and IWD (Russell 1000 Value). "
    "One analysis per ticker per calendar quarter (the latest) is kept so overlapping repeats are not "
    "counted twice. Intervals are 90% bootstrap intervals of the median excess (fixed seed). This is a "
    "prospective audit trail, not a proof of skill: with n of about 50 verdicts the interval is far "
    "wider than a 5%/year edge, so it cannot detect one; it can only flag a large, persistent failure. "
    "Value-tilted small caps against SPY also measure factor exposure, not skill; IWD is the fairer "
    "yardstick. With n < 10 in a group, do not conclude anything."
)


class HorizonResult(BaseModel):
    """Rentabilidad total de la acción y de cada benchmark en un horizonte, y el exceso resultante."""
    ret: float
    bench: dict[str, float]      # {"SPY": r, "IWD": r}
    excess: dict[str, float]     # ret − bench[b]


class OutcomeRow(BaseModel):
    """Resultado de un análisis en cada horizonte (None = pendiente o sin datos de precio)."""
    ticker: str
    analysis_date: str
    verdict: Optional[str]
    methodology: Optional[str]
    price_then: float           # cierre ajustado en (o justo antes de) la fecha del análisis
    days: int                   # días hasta hoy
    stock_dividends: bool       # False → dividendos no disponibles: retorno solo de precio
    bench_dividends: bool       # False → algún benchmark sin dividendos (solo precio)
    horizons: dict[str, Optional[HorizonResult]]


_MAX_STALE_DAYS = 7


def close_on_or_before(closes: dict[str, float], day: str) -> Optional[tuple[str, float]]:
    """Cierre del día `day` o del último día hábil anterior (fines de semana, festivos)."""
    eligible = [d for d in closes if d <= day]
    if not eligible:
        return None
    d = max(eligible)
    return d, closes[d]


def add_months(day: str, months: int) -> str:
    """Suma `months` meses calendario a una fecha ISO (el día se recorta al fin de mes: 31 ene + 1 → 28/29 feb)."""
    d = date.fromisoformat(day)
    idx = d.year * 12 + (d.month - 1) + months
    y, m = divmod(idx, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1])).isoformat()


def quarter_key(day: str) -> tuple[int, int]:
    """(año, trimestre natural 1–4) de una fecha ISO."""
    d = date.fromisoformat(day)
    return d.year, (d.month - 1) // 3 + 1


def dedup_quarterly(analyses: list[dict]) -> list[dict]:
    """Un análisis por (ticker, trimestre natural): el de fecha más reciente (empate → el último de la lista)."""
    best: dict[tuple, dict] = {}
    for a in analyses:
        key = (a["ticker"], *quarter_key(a["analysis_date"]))
        if key not in best or a["analysis_date"] >= best[key]["analysis_date"]:
            best[key] = a
    return sorted(best.values(), key=lambda a: (a["analysis_date"], a["ticker"]))


def total_return(closes: dict[str, float], dividends: Optional[list[tuple[str, float]]],
                 start: str, end: str) -> Optional[float]:
    """Rentabilidad total entre `start` y `end` con dividendos reinvertidos en la fecha ex-dividendo.

    `dividends` = [(fecha_ex ISO, importe por acción)]; None o [] → solo precio (quien llama distingue
    "no disponible" de "no paga" con el flag). Solo cuentan ex-fechas en (start, end]. Devuelve None si
    faltan cierres en algún extremo o si el último cierre es de más de 7 días antes del final.
    """
    a, b = close_on_or_before(closes, start), close_on_or_before(closes, end)
    if not (a and b) or a[1] <= 0:
        return None
    # Último cierre muy anterior al final (deslistada, adquirida): no se reutiliza como si siguiera cotizando
    # mientras el benchmark avanza (revisión del bloque C). 7 días cubren fines de semana y festivos.
    if (date.fromisoformat(end) - date.fromisoformat(b[0])).days > _MAX_STALE_DAYS:
        return None
    factor = b[1] / a[1]
    for ex, amount in dividends or []:
        if start < ex <= end:
            px = close_on_or_before(closes, ex)
            if px and px[1] > 0 and amount > 0:
                factor *= 1 + amount / px[1]
    return factor - 1


def outcome_rows(analyses: list[dict], closes_by_ticker: dict[str, dict[str, float]],
                 benchmark_closes: dict[str, dict[str, float]], today: str,
                 dividends_by_ticker: Optional[dict[str, Optional[list[tuple[str, float]]]]] = None,
                 ) -> list[OutcomeRow]:
    """Una fila por análisis con precio en la fecha del análisis; los demás se omiten (nada se inventa).

    Cada horizonte solo se rellena si ya transcurrió (`análisis + N meses <= hoy`) y hay precios en
    ambos extremos de acción y benchmarks; si no, queda None ("pending"). `dividends_by_ticker[t]`
    None / ausente = dividendos no disponibles (se marca por fila); [] = no paga dividendos.
    """
    divs = dividends_by_ticker or {}
    out: list[OutcomeRow] = []
    for a in analyses:
        t, start = a["ticker"], a["analysis_date"]
        closes = closes_by_ticker.get(t, {})
        then = close_on_or_before(closes, start)
        if not then or then[1] <= 0:
            continue
        horizons: dict[str, Optional[HorizonResult]] = {}
        for label in HORIZONS:
            end = today if label == "to_date" else add_months(start, HORIZON_MONTHS[label])
            if end > today or end <= start:
                horizons[label] = None
                continue
            ret = total_return(closes, divs.get(t), start, end)
            raw_bench = {b: total_return(benchmark_closes.get(b, {}), divs.get(b), start, end) for b in BENCHMARKS}
            bench = {b: v for b, v in raw_bench.items() if v is not None}
            if ret is None or len(bench) < len(BENCHMARKS):     # falta una rentabilidad: horizonte sin dato
                horizons[label] = None
                continue
            horizons[label] = HorizonResult(
                ret=round(ret, 4), bench={b: round(v, 4) for b, v in bench.items()},
                excess={b: round(ret - v, 4) for b, v in bench.items()})
        out.append(OutcomeRow(
            ticker=t, analysis_date=start, verdict=a.get("verdict"),
            methodology=a.get("methodology_version") or "pre-H1", price_then=then[1],
            days=(date.fromisoformat(today) - date.fromisoformat(start)).days,
            stock_dividends=divs.get(t) is not None,
            bench_dividends=all(divs.get(b) is not None for b in BENCHMARKS),
            horizons=horizons))
    return out


def bootstrap_median_ci(values: list[float], level: float = 0.90,
                        resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED
                        ) -> Optional[tuple[float, float]]:
    """Intervalo bootstrap percentil de la mediana. Determinista (semilla fija). None si no hay datos.

    Con n pequeño el intervalo es muy ancho: es justo el mensaje. No hace falta supuesto de normalidad.
    """
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    meds = sorted(median(rng.choices(values, k=n)) for _ in range(resamples))
    lo = meds[int(((1 - level) / 2) * resamples)]
    hi = meds[min(resamples - 1, int((1 - (1 - level) / 2) * resamples))]
    return lo, hi


def summarize(rows: list[OutcomeRow]) -> list[dict]:
    """Por (metodología, veredicto, horizonte): n, mediana del exceso frente a cada benchmark con IC 90 %.

    `n` cuenta solo filas con ese horizonte transcurrido; `pending` las que aún no. `too_few` = n < 10.
    """
    groups: dict[tuple[str, str], list[OutcomeRow]] = {}
    for r in rows:
        groups.setdefault((r.methodology or "pre-H1", r.verdict or "n/a"), []).append(r)
    out: list[dict] = []
    for (m, v), rs in sorted(groups.items()):
        for h in HORIZONS:
            done = [hr for x in rs if (hr := x.horizons.get(h)) is not None]
            entry: dict = {"methodology": m, "verdict": v, "horizon": h, "n": len(done),
                           "pending": len(rs) - len(done), "too_few": len(done) < MIN_N}
            for b in BENCHMARKS:
                ex = [d.excess[b] for d in done]
                ci = bootstrap_median_ci(ex)            # None solo si `ex` está vacío
                entry[b] = ({"median_excess": round(median(ex), 4),
                             "ci": tuple(round(c, 4) for c in ci),
                             "pct_beat": round(sum(e > 0 for e in ex) / len(ex), 2)} if ex and ci else None)
            out.append(entry)
    return out
