"""
Scores contables deterministas (bloque A, A2, ADR-043): Piotroski F, Altman Z'', Beneish M y
accruals de Sloan, sobre los estados XBRL ya extraídos.

Por qué existen: la evidencia académica más sólida de value investing no es un DCF preciso, sino
separar las baratas que mejoran de las trampas de valor y detectar contabilidad agresiva:
Piotroski (2000), Altman (1968; Z'' de 1995 para no manufactureras), Beneish (1999), Sloan (1996).
Son cálculos con fórmula publicada, sin LLM. Función pura: recibe filas por año (más reciente
primero) y devuelve un AccountingScores. Regla de oro: si falta un insumo, el score sale "n/a" con
la lista de lo que falta; nunca se sustituye por un valor neutro (invariante 5, fail loud).
"""
from __future__ import annotations

from typing import Optional

from due_diligence.schemas.base import AccountingScores, ScoreResult

# Umbrales publicados (no calibrados por nosotros).
_Z2_SAFE, _Z2_DISTRESS = 2.60, 1.10      # Altman Z'' (1995): >2,60 zona segura; <1,10 zona de estrés
_BENEISH_CUTOFF = -1.78                  # Beneish (1999), modelo de 8 variables


def _get(row: dict, key: str) -> Optional[float]:
    v = row.get(key)
    return float(v) if v is not None else None


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return a / b if a is not None and b not in (None, 0) else None


def _na(name: str, period: str, missing: list[str]) -> ScoreResult:
    return ScoreResult(name=name, value=None, period=period,
                       reading=f"n/a — missing: {', '.join(sorted(set(missing)))}")


def piotroski_f(rows: list[dict]) -> ScoreResult:
    """Piotroski F-score (0–9) del último año frente al anterior.

    Señales (Piotroski 2000): ROA>0, CFO>0, ΔROA>0, CFO>NI, Δapalancamiento<0, Δliquidez>0,
    sin emisión de acciones, Δmargen bruto>0, Δrotación de activos>0. ROA y rotación usan los
    activos al INICIO del año. Aproximaciones documentadas: apalancamiento = deuda total/activos
    (Piotroski usa deuda a largo / activos medios) y "sin emisión" = acciones medias básicas que no
    suben (recompras incluidas). Si falta alguna señal: "x of n available" sin clasificar.
    """
    name = "Piotroski F-score"
    if len(rows) < 2:
        return _na(name, rows[0]["period"] if rows else "n/a", ["previous fiscal year"])
    t, p = rows[0], rows[1]
    pp = rows[2] if len(rows) > 2 else {}
    period = f"{t['period']} vs {p['period']}"
    roa_t = _div(_get(t, "net_income"), _get(p, "total_assets"))
    roa_p = _div(_get(p, "net_income"), _get(pp, "total_assets"))
    signals: dict[str, Optional[bool]] = {
        "ROA > 0": None if roa_t is None else roa_t > 0,
        "CFO > 0": None if _get(t, "ocf") is None else _get(t, "ocf") > 0,
        "ΔROA > 0": None if roa_t is None or roa_p is None else roa_t > roa_p,
        "CFO > net income": (None if _get(t, "ocf") is None or _get(t, "net_income") is None
                             else _get(t, "ocf") > _get(t, "net_income")),
    }
    lev_t, lev_p = _div(_get(t, "total_debt"), _get(t, "total_assets")), _div(_get(p, "total_debt"), _get(p, "total_assets"))
    signals["Leverage down"] = None if lev_t is None or lev_p is None else lev_t < lev_p
    cr_t = _div(_get(t, "current_assets"), _get(t, "current_liabilities"))
    cr_p = _div(_get(p, "current_assets"), _get(p, "current_liabilities"))
    signals["Current ratio up"] = None if cr_t is None or cr_p is None else cr_t > cr_p
    signals["No share issuance"] = (None if _get(t, "shares") is None or _get(p, "shares") is None
                                    else _get(t, "shares") <= _get(p, "shares"))
    gm_t, gm_p = _div(_get(t, "gross_profit"), _get(t, "revenue")), _div(_get(p, "gross_profit"), _get(p, "revenue"))
    signals["Gross margin up"] = None if gm_t is None or gm_p is None else gm_t > gm_p
    at_t = _div(_get(t, "revenue"), _get(p, "total_assets"))
    at_p = _div(_get(p, "revenue"), _get(pp, "total_assets"))
    signals["Asset turnover up"] = None if at_t is None or at_p is None else at_t > at_p

    available = {k: v for k, v in signals.items() if v is not None}
    score = sum(available.values())
    missing = [k for k, v in signals.items() if v is None]
    comps = {k: (1.0 if v else 0.0) if v is not None else None for k, v in signals.items()}
    if missing:
        reading = f"{score} of {len(available)} available signals (missing: {', '.join(missing)})"
        return ScoreResult(name=name, value=None, period=period, reading=reading, components=comps)
    label = "strong (8–9)" if score >= 8 else "weak (0–2)" if score <= 2 else "neutral (3–7)"
    return ScoreResult(name=name, value=float(score), period=period, reading=f"{score}/9 — {label}",
                       components=comps)


def altman_z2(rows: list[dict]) -> ScoreResult:
    """Altman Z'' (1995, no manufactureras): 6,56·X1 + 3,26·X2 + 6,72·X3 + 1,05·X4.

    X1 = capital circulante/activos, X2 = beneficios retenidos/activos, X3 = EBIT/activos,
    X4 = patrimonio contable/pasivo total (pasivo = activos − patrimonio). Con patrimonio ≤ 0
    (recompras masivas: AZO, MCD) X4 pierde sentido → "n/m", no una falsa zona de estrés.
    """
    name = "Altman Z''"
    t = rows[0] if rows else {}
    period = t.get("period", "n/a")
    need = {k: _get(t, k) for k in ("total_assets", "current_assets", "current_liabilities",
                                     "retained_earnings", "ebit", "equity")}
    missing = [k for k, v in need.items() if v is None]
    if missing:
        return _na(name, period, missing)
    ta, eq = need["total_assets"], need["equity"]
    if eq <= 0:
        return ScoreResult(name=name, value=None, period=period,
                           reading="n/m — book equity ≤ 0 (e.g. heavy buybacks): X4 is not meaningful")
    liabilities = ta - eq
    x = {"X1 working capital/assets": (need["current_assets"] - need["current_liabilities"]) / ta,
         "X2 retained earnings/assets": need["retained_earnings"] / ta,
         "X3 EBIT/assets": need["ebit"] / ta,
         "X4 book equity/liabilities": eq / liabilities if liabilities > 0 else None}
    if x["X4 book equity/liabilities"] is None:
        return _na(name, period, ["total liabilities"])
    z = (6.56 * x["X1 working capital/assets"] + 3.26 * x["X2 retained earnings/assets"]
         + 6.72 * x["X3 EBIT/assets"] + 1.05 * x["X4 book equity/liabilities"])
    zone = "safe" if z > _Z2_SAFE else "distress" if z < _Z2_DISTRESS else "grey"
    return ScoreResult(name=name, value=round(z, 2), period=period,
                       reading=f"{z:.2f} — {zone} zone (safe > {_Z2_SAFE}, distress < {_Z2_DISTRESS})",
                       components={k: round(v, 4) for k, v in x.items()})


def beneish_m(rows: list[dict]) -> ScoreResult:
    """Beneish M-score de 8 variables (1999); M > −1,78 = posible manipulación de beneficios.

    M = −4,84 + 0,920·DSRI + 0,528·GMI + 0,404·AQI + 0,892·SGI + 0,115·DEPI − 0,172·SGAI
        + 4,679·TATA − 0,327·LVGI.
    Aproximaciones documentadas: TATA = (beneficio neto − CFO)/activos (versión simplificada
    habitual), AQI sin valores negociables y LVGI con deuda total + pasivo corriente. Es una
    BANDERA para mirar más de cerca, no una acusación: tiene falsos positivos en empresas que crecen
    muy deprisa (SGI alto).
    """
    name = "Beneish M-score"
    if len(rows) < 2:
        return _na(name, rows[0]["period"] if rows else "n/a", ["previous fiscal year"])
    t, p = rows[0], rows[1]
    period = f"{t['period']} vs {p['period']}"
    keys = ("receivables", "revenue", "gross_profit", "current_assets", "ppe_net", "total_assets",
            "da", "sga", "current_liabilities", "total_debt")
    missing = [f"{k} {r['period']}" for r in (t, p) for k in keys if _get(r, k) is None]
    missing += [f"{k} {t['period']}" for k in ("net_income", "ocf") if _get(t, k) is None]
    if missing:
        return _na(name, period, missing)
    g = lambda r, k: _get(r, k)  # noqa: E731
    try:
        dsri = (g(t, "receivables") / g(t, "revenue")) / (g(p, "receivables") / g(p, "revenue"))
        gmi = (g(p, "gross_profit") / g(p, "revenue")) / (g(t, "gross_profit") / g(t, "revenue"))
        aqi = ((1 - (g(t, "current_assets") + g(t, "ppe_net")) / g(t, "total_assets"))
               / (1 - (g(p, "current_assets") + g(p, "ppe_net")) / g(p, "total_assets")))
        sgi = g(t, "revenue") / g(p, "revenue")
        depi = ((g(p, "da") / (g(p, "da") + g(p, "ppe_net"))) / (g(t, "da") / (g(t, "da") + g(t, "ppe_net"))))
        sgai = (g(t, "sga") / g(t, "revenue")) / (g(p, "sga") / g(p, "revenue"))
        tata = (g(t, "net_income") - g(t, "ocf")) / g(t, "total_assets")
        lvgi = (((g(t, "current_liabilities") + g(t, "total_debt")) / g(t, "total_assets"))
                / ((g(p, "current_liabilities") + g(p, "total_debt")) / g(p, "total_assets")))
    except ZeroDivisionError:
        return _na(name, period, ["a zero denominator (receivables, gross profit, SG&A or asset base)"])
    comps = {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi, "DEPI": depi, "SGAI": sgai,
             "TATA": tata, "LVGI": lvgi}
    m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi + 0.115 * depi
         - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    reading = (f"{m:.2f} — above {_BENEISH_CUTOFF}: possible earnings manipulation flag (look closer; "
               f"fast growth also triggers it)" if m > _BENEISH_CUTOFF
               else f"{m:.2f} — below {_BENEISH_CUTOFF}: no manipulation flag")
    return ScoreResult(name=name, value=round(m, 2), period=period, reading=reading,
                       components={k: round(v, 4) for k, v in comps.items()})


def sloan_accruals(rows: list[dict]) -> ScoreResult:
    """Accruals de Sloan (1996): (beneficio neto − CFO) / activos medios.

    Positivo = el beneficio contable supera la caja generada; Sloan mostró que los beneficios
    con muchos accruals persisten menos. Se informa el valor; no hay umbral universal publicado.
    """
    name = "Sloan accruals"
    if len(rows) < 2:
        return _na(name, rows[0]["period"] if rows else "n/a", ["previous fiscal year"])
    t, p = rows[0], rows[1]
    ni, cfo, ta_t, ta_p = (_get(t, "net_income"), _get(t, "ocf"), _get(t, "total_assets"),
                           _get(p, "total_assets"))
    missing = [k for k, v in (("net_income", ni), ("ocf", cfo), ("total_assets", ta_t),
                              ("previous total_assets", ta_p)) if v is None]
    if missing:
        return _na(name, t["period"], missing)
    acc = (ni - cfo) / ((ta_t + ta_p) / 2)
    side = "above" if acc > 0 else "below"
    return ScoreResult(name=name, value=round(acc, 4), period=t["period"],
                       reading=f"{acc:+.1%} of assets — earnings {side} operating cash flow")


# Series anuales del raw → clave de fila. Magnitudes: el raw guarda OCF, D&A y SG&A con su signo
# XBRL habitual (positivo); acciones = media ponderada básica (proxy de emisión, Piotroski F7).
_ANNUAL_KEYS = {"revenue": "revenue", "gross_profit": "gross_profit", "operating_income": "ebit",
                "net_income": "net_income", "operating_cash_flow": "ocf",
                "depreciation_amortization": "da", "sga": "sga", "basic_shares_weighted": "shares"}
_BALANCE_KEYS = {"total_assets": "total_assets", "current_assets": "current_assets",
                 "current_liabilities": "current_liabilities", "total_debt": "total_debt",
                 "stockholders_equity": "equity", "retained_earnings": "retained_earnings",
                 "ppe_net": "ppe_net", "receivables": "receivables"}


def rows_from_raw(raw: dict) -> list[dict]:
    """Filas por ejercicio (más reciente primero) uniendo series anuales y balance_history del raw.

    Solo años fiscales CONSECUTIVOS desde el último: un hueco cortaría la comparación t/t−1 y los
    scores mezclarían años no contiguos (misma lección que EC-96).
    """
    by_period: dict[str, dict] = {}
    for src_key, key in _ANNUAL_KEYS.items():
        for item in raw.get("annual", {}).get(src_key, []) or []:
            if item.get("value") is not None:
                by_period.setdefault(item["period"], {})[key] = abs(item["value"]) if key in (
                    "da", "sga") else item["value"]
    for h in raw.get("balance_history", []) or []:
        row = by_period.setdefault(h["period"], {})
        for src_key, key in _BALANCE_KEYS.items():
            if h.get(src_key) is not None:
                row[key] = h[src_key]
    years = sorted((int(p[2:]) for p in by_period if p.startswith("FY") and p[2:].isdigit()), reverse=True)
    rows: list[dict] = []
    for y in years:
        if rows and int(rows[-1]["period"][2:]) - y != 1:
            break
        rows.append({"period": f"FY{y}", **by_period[f"FY{y}"]})
    return rows


def compute_accounting_scores(rows: list[dict]) -> AccountingScores:
    """Los cuatro scores sobre filas por año (más reciente primero, años fiscales consecutivos)."""
    return AccountingScores(piotroski=piotroski_f(rows), altman_z2=altman_z2(rows),
                            beneish_m=beneish_m(rows), sloan_accruals=sloan_accruals(rows))
