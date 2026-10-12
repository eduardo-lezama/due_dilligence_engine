"""
periods — aritmética de periodos fiscales para series MetricValue.

Por qué: varias funciones asumían que índice i = i años atrás y emparejaban
series por posición (zip). Con huecos (BBW sin FY2025) eso producía FCF de años
cruzados (EC-82) y CAGRs con exponente erróneo (EC-83). Aquí todo se alinea por
etiqueta 'FY2025'. Lo usan edgar.parse_financials, financials, valuation y la síntesis.
"""
from __future__ import annotations

import math
import re

import structlog

from due_diligence.schemas.base import MetricValue

log = structlog.get_logger()


def fiscal_year(period: str) -> int:
    """'FY2025' → 2025. ValueError si la etiqueta no es anual."""
    m = re.fullmatch(r"FY ?(\d{4})", period)
    if m is None:
        raise ValueError(f"not a fiscal-year label: {period!r}")
    return int(m.group(1))


def align_by_period(a: list[MetricValue], b: list[MetricValue]) -> list[tuple[MetricValue, MetricValue]]:
    """Pares (a, b) del MISMO periodo, en el orden de `a`. Periodos sin par se omiten.

    Sustituye a zip(a, b): zip empareja por posición y, si una serie tiene un hueco,
    combina años distintos sin avisar.
    """
    b_by = {x.period: x for x in b}
    return [(x, b_by[x.period]) for x in a if x.period in b_by]


def cagr_by_fiscal_years(series: list[MetricValue], max_years: int | None = None,
                         scan_for_positive_start: bool = False) -> tuple[float, int] | None:
    """CAGR entre el punto más reciente y el más antiguo dentro de la ventana.

    n = diferencia REAL de años fiscales (no de índices). `max_years` acota la ventana
    (p.ej. 5 → como mucho un CAGR a 5 años aunque la serie tenga 10).
    scan_for_positive_start=True avanza hacia puntos más recientes si el inicio es <= 0
    (semántica EC-60 de FCF); con False, un inicio <= 0 devuelve None.
    Si las etiquetas no son FY únicas (datos sintéticos) → fallback posicional
    con warning, preservando el comportamiento anterior en vez de romper.

    Returns: (cagr, n_years) o None si no hay ventana válida.
    """
    pts = [(m.period, m.value) for m in series if m.value is not None]
    try:
        years = [fiscal_year(p) for p, _ in pts]
        if len(set(years)) != len(years):
            raise ValueError("duplicate fiscal years")
    except ValueError:
        log.warning("cagr_positional_fallback", periods=[p for p, _ in pts])
        years = [-i for i in range(len(pts))]          # índice como distancia (legacy)
    data = sorted(zip(years, (v for _, v in pts)), reverse=True)  # zip aceptado: misma serie
    if len(data) < 2 or data[0][1] <= 0:
        return None
    y0, v0 = data[0]
    window = sorted((y, v) for y, v in data[1:] if max_years is None or y0 - y <= max_years)
    for y, v in window:                                  # del más antiguo al más reciente
        if v > 0:
            n = y0 - y
            return (v0 / v) ** (1 / n) - 1, n
        if not scan_for_positive_start:
            return None
    return None


def value_at(series: list[MetricValue], period: str) -> float | None:
    """Valor de la serie en el ejercicio EXACTO `period`, o None si ese año no está.

    Para cocientes entre series (recompras / FCF, …): `serie[0]` no garantiza el mismo año
    si una de las dos tiene huecos (revisión final H1, #9/#10; mismo patrón que EC-82).
    """
    return next((m.value for m in series if m.period == period and m.value is not None), None)


def least_squares_growth(series: list[MetricValue], max_years: int = 10,
                         min_points: int = 3) -> tuple[float, int] | None:
    """Tasa de crecimiento por mínimos cuadrados (ajuste log-lineal) en una ventana de años fiscales.

    Por qué (bloque A, ADR-043): el CAGR entre dos extremos depende de en qué año cae el inicio.
    Con 5 años, la ventana empezaba en 2020 y heredaba el rebote post-COVID (XOM 13,2 %, CVX 14,3 %,
    PFE 8,3 % por la vacuna). El "least-squares growth rate" (el método del Banco Mundial para el
    crecimiento del PIB) usa TODOS los puntos: ln(v) = a + b·año → g = e^b − 1. Con 10 años cubre
    un ciclo: XOM 5,9 %, CVX 6,0 %, PFE 3,2 %; en negocios estables coincide con el CAGR (MSFT 14,9 %).
    Solo valores > 0 (logaritmo). Returns (g, años entre el punto más antiguo y el más reciente)
    o None con menos de `min_points` puntos válidos.
    """
    try:
        pts = [(fiscal_year(m.period), m.value) for m in series if m.value is not None and m.value > 0]
    except ValueError:
        return None   # etiquetas no FY (datos sintéticos/legacy): el llamador cae al CAGR (revisión #4)
    if not pts:
        return None
    y0 = max(y for y, _ in pts)
    pts = [(y, math.log(v)) for y, v in pts if y0 - y <= max_years]
    if len(pts) < min_points:
        return None
    mx = sum(y for y, _ in pts) / len(pts)
    my = sum(v for _, v in pts) / len(pts)
    sxx = sum((y - mx) ** 2 for y, _ in pts)
    if sxx == 0:
        return None
    slope = sum((y - mx) * (v - my) for y, v in pts) / sxx
    return math.exp(slope) - 1, y0 - min(y for y, _ in pts)
