"""
xbrl_extract — lógica pura de extracción XBRL (sin red) para EdgarAdapter.

Por qué existe: fetch_raw mezclaba I/O con edgartools y la lógica de selección de
conceptos/periodos dentro de closures imposibles de testear (los fixtures son su
OUTPUT). Aquí todo recibe DataFrames + un FactsLookup y es testeable con datos
sintéticos. fetch_raw (edgar.py) solo descarga y delega en build_raw().

Excepción documentada a "No bare dict returns": build_raw devuelve el dict `raw`
porque ese es el contrato histórico entre fetch_raw y parse_financials (los
fixtures JSON de tests/fixtures/ son exactamente ese dict).
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol

import pandas as pd
import structlog

log = structlog.get_logger()


class DataUnavailableError(Exception):
    """Métrica requerida ausente o inconsistente en la fuente (fail loud, ADR-005).

    Vive aquí (capa pura) y edgar.py la re-exporta para no romper los imports
    existentes `from due_diligence.tools.edgar import DataUnavailableError`.
    """


# Columnas anuales de edgartools: "FY 2025" (con espacio). Nuestras etiquetas: "FY2025".
_FY_COL = re.compile(r"^FY (\d{4})$")


# --------------------------------------------------------------------------- #
# Etiquetas de periodo
# --------------------------------------------------------------------------- #

def fy_label_from_period_end(period_end: date) -> str:
    """Etiqueta FY de un cierre fiscal, MISMA regla que edgartools 5.30
    (enhanced_statement.py:1756-1772 + validador :1162-1210): el año del period_end,
    salvo cierres 1-7 enero (calendarios 52/53 semanas) → año anterior.

    Por qué copiar la regla: si nuestra etiqueta del 10-K y las columnas de los
    estados usaran reglas distintas, el invariante de build_raw dispararía en falso
    (o peor, mezclaríamos años sin darnos cuenta — EC-87).
    """
    if period_end.month == 1 and period_end.day <= 7:
        return f"FY{period_end.year - 1}"
    return f"FY{period_end.year}"


def fy_year(label: str) -> int:
    """'FY2025' o 'FY 2025' → 2025. ValueError si no es etiqueta anual."""
    m = re.fullmatch(r"FY ?(\d{4})", label)
    if m is None:
        raise ValueError(f"not a fiscal-year label: {label!r}")
    return int(m.group(1))


def fy_columns(df: pd.DataFrame) -> list[str]:
    """Columnas 'FY YYYY' ordenadas por AÑO numérico desc (no por string).

    Las columnas de metadata (label, is_total, depth...) se ignoran.
    """
    cols = [c for c in df.columns if isinstance(c, str) and _FY_COL.match(c)]
    return sorted(cols, key=fy_year, reverse=True)


# --------------------------------------------------------------------------- #
# Lectura de filas y valores
# --------------------------------------------------------------------------- #

def _row(df: pd.DataFrame, concept: str) -> pd.Series | None:
    """Fila de un concepto; si hay duplicados (DataFrame) toma la primera.

    Algunos filers usan el mismo concepto en varios contextos y df.loc devuelve
    un DataFrame en lugar de una Series.
    """
    if concept not in df.index:
        return None
    row = df.loc[concept]
    return row.iloc[0] if isinstance(row, pd.DataFrame) else row


def _to_float(val: Any) -> float | None:
    """float o None (NaN/None/no numérico → None). Nunca 0 implícito."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def select_period_value(row: pd.Series, period: str) -> float | None:
    """Valor EXACTO de la columna del periodo pedido; None si falta.

    Sustituye a _get_point, que devolvía el primer año no-NaN etiquetado con
    `period` (EC-78: un balance de FY2024 aparecía como FY2025).
    """
    col = f"FY {fy_year(period)}"
    return _to_float(row.get(col)) if col in row.index else None


def annual_series(df: pd.DataFrame, concepts: tuple[str, ...], n: int = 10) -> list[dict]:
    """Serie anual (index 0 = más reciente) del PRIMER concepto con datos.

    Cada item: {value, period 'FY2025', source=concepto}. Los NaN se omiten
    (los huecos interiores los rellena backfill_gaps desde raw facts).
    """
    for concept in concepts:
        row = _row(df, concept)
        if row is None:
            continue
        out = []
        for col in fy_columns(df)[:n]:
            v = _to_float(row.get(col))
            if v is not None:
                out.append({"value": v, "period": col.replace(" ", ""), "source": concept})
        if out:
            return out
    return []


# --------------------------------------------------------------------------- #
# Raw facts (fallback a la vista curada — EC-37)
# --------------------------------------------------------------------------- #

class FactsLookup(Protocol):
    """Acceso a raw facts anuales por (concepto us-gaap sin prefijo, etiqueta FY).

    Es la seam de test: en producción EdgarFactsLookup, en tests un FakeFacts.
    """

    def annual_value(self, concept: str, period: str) -> float | None: ...


class EdgarFactsLookup:
    """FactsLookup sobre EntityFacts ya descargado (sin red).

    Empareja por period_end → etiqueta (fy_label_from_period_end), NO por fiscal_year:
    el fiscal_year de companyfacts es el del filing, y los comparativos de un 10-K
    comparten fiscal_year con el año corriente (bug latente de get_annual_fact).
    """

    def __init__(self, facts: Any) -> None:
        self._by_concept: dict[str, list[Any]] = {}
        for f in facts.get_all_facts():          # entity_facts.py:237
            if (f.fiscal_period != "FY" or f.numeric_value is None
                    or f.dimensions or f.period_end is None):
                continue                          # solo anuales, no dimensionales
            self._by_concept.setdefault(f.concept, []).append(f)

    def annual_value(self, concept: str, period: str) -> float | None:
        """Valor anual de `concept` cuyo period_end cae en la etiqueta `period`."""
        cands = [
            f for f in self._by_concept.get(f"us-gaap:{concept}", [])
            if str(f.form_type).startswith("10-K")
            and fy_label_from_period_end(f.period_end) == period
            # duraciones: solo anuales (>300d, mismo umbral que edgartools :1803)
            and (f.period_start is None or (f.period_end - f.period_start).days > 300)
        ]
        if not cands:
            return None
        # Restatements: varios filings reportan el mismo periodo → gana el más reciente.
        best = max(cands, key=lambda f: f.filing_date or date.min)
        return float(best.numeric_value)


def point_value(df: pd.DataFrame, concepts: tuple[str, ...], period: str,
                facts: FactsLookup | None = None) -> dict | None:
    """Valor puntual (balance) del periodo EXACTO: vista curada → raw facts (EC-37).

    Devuelve {value, period, source} o None. `source` es el concepto curado o
    "facts:<concepto>" cuando vino del fallback, para que sea auditable.
    """
    for concept in concepts:
        row = _row(df, concept)
        if row is None:
            continue
        v = select_period_value(row, period)
        if v is not None:
            return {"value": v, "period": period, "source": concept}
    if facts is not None:
        for concept in concepts:
            v = facts.annual_value(concept, period)
            if v is not None:
                log.info("balance_raw_facts_fallback_used", concept=concept, period=period)
                return {"value": v, "period": period, "source": f"facts:{concept}"}
    return None


# --------------------------------------------------------------------------- #
# Huecos de años (EC-88) y columnas trimestrales (EC-84) — T7
# --------------------------------------------------------------------------- #

def backfill_gaps(series: list[dict], facts: FactsLookup) -> list[dict]:
    """Rellena años INTERIORES ausentes desde raw facts del mismo concepto.

    Caso real: BBW FY2025, que edgartools descarta en su validador fiscal_year/period_end
    (enhanced_statement.py:1548). No extrapola fuera del rango observado y no toca
    series derivadas ("derived:...") ni ya rellenadas ("facts:..."): no son conceptos
    consultables uno a uno.
    """
    if len(series) < 2:
        return series
    # Concepto de la serie: el primer punto con un concepto curado. "facts:X" / "derived:..." no son
    # consultables uno a uno; si el ÚLTIMO punto viene de facts (prefer_total_revenue, EC-105) se usa el
    # siguiente (revisión de código: antes se abandonaba el relleno → regresión de EC-88).
    concept = next((i["source"] for i in series if ":" not in i["source"]), None)
    if concept is None:
        return series
    by_year = {fy_year(i["period"]): i for i in series}
    hi, lo = max(by_year), min(by_year)
    for y in range(hi - 1, lo, -1):
        if y not in by_year:
            v = facts.annual_value(concept, f"FY{y}")
            if v is not None:
                by_year[y] = {"value": v, "period": f"FY{y}", "source": f"facts:{concept}"}
    return [by_year[y] for y in sorted(by_year, reverse=True)]


def merge_annual(concepts: tuple[str, ...], dfs: tuple[pd.DataFrame, ...], facts: FactsLookup,
                 labels: list[str], n: int = 10) -> list[dict]:
    """Serie anual fusionando la cadena de conceptos AÑO A AÑO (EC-90).

    Para cada etiqueta de `labels` (las del revenue: mismas fechas que el resto), toma el
    PRIMER concepto de la cadena que tenga dato en ESE año: primero en las vistas curadas
    (`dfs`, en orden), luego en los facts en bruto. Por qué: las empresas cambian de concepto
    con el tiempo (PRDO pasó de PaymentsToAcquirePropertyPlantAndEquipment a
    PaymentsForCapitalImprovements). La versión anterior escogía el primer concepto con ALGÚN
    dato y se quedaba con él: el capex de PRDO quedaba anclado a FY2016 y el FCF también.
    Cada punto lleva su `source` real (concepto curado o "facts:<concepto>").
    """
    out: list[dict] = []
    for label in labels[:n]:
        col = f"FY {fy_year(label)}"
        hit: dict | None = None
        for concept in concepts:                       # 1) vistas curadas, en orden de preferencia
            for df in dfs:
                row = _row(df, concept)
                if row is not None and col in row.index and (v := _to_float(row.get(col))) is not None:
                    hit = {"value": v, "period": label, "source": concept}
                    break
            if hit:
                break
        if hit is None:                                # 2) facts en bruto, mismo orden
            for concept in concepts:
                if (v := facts.annual_value(concept, label)) is not None:
                    hit = {"value": v, "period": label, "source": f"facts:{concept}"}
                    break
        if hit is not None:
            out.append(hit)
    return out


def _by_period(series: list[dict]) -> dict[str, dict]:
    return {i["period"]: i for i in series}


def _sorted_desc(by: dict[str, dict]) -> list[dict]:
    return [by[p] for p in sorted(by, key=fy_year, reverse=True)]


def derive_sga(sga: list[dict], inc_df: pd.DataFrame, facts: FactsLookup, labels: list[str]) -> list[dict]:
    """SG&A publicado; para los años sin él, ventas y marketing + generales y administración,
    solo si están LOS DOS ese año (una suma parcial infravaloraría el gasto)."""
    by = _by_period(sga)
    parts = [_by_period(merge_annual((c,), (inc_df,), facts, labels)) for c in SGA_PARTS]
    for label in labels:
        if label not in by and all(label in part for part in parts):
            by[label] = {"value": sum(part[label]["value"] for part in parts), "period": label,
                         "source": "derived:" + "+".join(SGA_PARTS)}
    return _sorted_desc(by)


_TOTAL_REVENUE_GAP = 0.10   # EC-105: por encima, el concepto elegido mide otra cosa (bruto/componente)


def prefer_total_revenue(revenue: list[dict], facts: FactsLookup) -> list[dict]:
    """Usa la línea total `Revenues` (raw facts) cuando el concepto elegido difiere > 10 % ese año.

    Por qué (EC-105): MA etiquetaba en 2018–2021 sus ingresos BRUTOS (antes de rebates, que presenta
    como menor ingreso) con RevenueFromContract… y los netos con Revenues; la cadena elegía el bruto y
    la serie mezclaba bruto (2018–21) y neto (2022+): CAGR a 5 años 6,8 % con ventas al ~14 %. ADP
    2017–18 y PFE 2023 tenían el mismo problema con un componente parcial.
    Por qué no "siempre Revenues": medido en 33 empresas, Revenues a veces incluye otros ingresos
    (CVX, XOM ~3 %) o difiere por alcance (KO, MCD ±1–4 %). Una diferencia pequeña es de alcance y
    cualquiera de los dos es defendible; una grande (> 10 %) es otra magnitud. Solo se cambia esa.
    """
    out = []
    for item in revenue:
        concept = item["source"].removeprefix("facts:")
        total = facts.annual_value("Revenues", item["period"]) if concept != "Revenues" else None
        if total is not None and item["value"] and abs(total - item["value"]) > _TOTAL_REVENUE_GAP * abs(item["value"]):
            gap = (item["value"] - total) / total
            log.info("total_revenue_line_preferred", period=item["period"], concept=concept, gap=f"{gap:+.0%}")
            out.append({"value": total, "period": item["period"],
                        "source": f"facts:Revenues (total line; {concept} differed {gap:+.0%})"})
        else:
            out.append(item)
    return out


NET_INCOME = ("NetIncomeLoss",)
PROFIT_LOSS = ("ProfitLoss",)
NCI_INCOME = ("NetIncomeLossAttributableToNoncontrollingInterest",)


def resolve_net_income(inc_df: pd.DataFrame, facts: FactsLookup, labels: list[str],
                       n: int = 10) -> list[dict]:
    """Beneficio neto atribuible a la matriz, año a año sobre la rejilla del revenue (EC-103).

    Por qué: algunas empresas no publican `NetIncomeLoss` y solo dan `ProfitLoss`, que INCLUYE la
    parte de los minoritarios (MA, CAT: serie vacía; TGT: la mitad de los años). Por año:
      1. NetIncomeLoss (vista curada → raw facts), como hasta ahora;
      2. si no, ProfitLoss − NetIncomeLossAttributableToNoncontrollingInterest (derivado, con
         la fórmula en `source`);
      3. si no hay dato de minoritarios ese año, ProfitLoss tal cual y etiquetado: sin
         minoritarios publicados, ProfitLoss es el beneficio de la matriz.
    """
    # Rejilla = años del revenue ∪ columnas del estado de resultados: el beneficio neto puede
    # cubrir más años que el revenue (PFE: 8 de revenue, 10 de beneficio) y no debe recortarse.
    grid = sorted(set(labels) | {c.replace(" ", "") for c in fy_columns(inc_df)},
                  key=fy_year, reverse=True)[:n]

    def by_label(concepts: tuple[str, ...]) -> dict[str, dict]:
        return {i["period"]: i for i in merge_annual(concepts, (inc_df,), facts, grid, n)}

    direct, profit, nci = by_label(NET_INCOME), by_label(PROFIT_LOSS), by_label(NCI_INCOME)
    out: list[dict] = []
    for label in grid:
        if label in direct:
            out.append(direct[label])
        elif label in profit and label in nci:
            out.append({"value": profit[label]["value"] - nci[label]["value"], "period": label,
                        "source": f"derived:ProfitLoss-{NCI_INCOME[0]}"})
        elif label in profit:
            out.append({**profit[label], "source": "ProfitLoss (no noncontrolling interest reported)"})
    return out


def extract_annual(concepts: tuple[str, ...], dfs: tuple[pd.DataFrame, ...], facts: FactsLookup,
                   labels: list[str], n: int = 10) -> list[dict]:
    """Serie anual para conceptos que la vista curada puede omitir (interest, SBC, D&A, shares).

    Delegado en merge_annual (EC-90): fusión año a año curado → facts, con las etiquetas
    del revenue para que el FCFF pueda alinear por periodo.
    """
    return merge_annual(concepts, dfs, facts, labels, n)


_Q_XBRL = re.compile(r"^(\d{4}-\d{2}-\d{2}) \((Q[1-4])\)$")   # xbrl/statements.py:1111 — verificado en vivo
_Q_FACTS = re.compile(r"^(Q[1-4]) (\d{4})$")                  # enhanced_statement.py:1779


def latest_quarter_column(columns: list[Any]) -> str | None:
    """Columna trimestral más reciente por FECHA (no por string; 'Q4 2025' > 'Q1 2026'
    alfabéticamente). Ignora YTD/FY. Acepta ambos formatos de edgartools:
    '2025-06-28 (Q3)' (XBRL del 10-Q) y 'Q3 2025' (EntityFacts)."""
    keyed: list[tuple[date, str]] = []
    for c in columns:
        if not isinstance(c, str):
            continue
        if m := _Q_XBRL.match(c):
            keyed.append((date.fromisoformat(m.group(1)), c))
        elif m := _Q_FACTS.match(c):
            # Aproximación: fin del trimestre natural (Qn → mes 3n) — solo para ordenar.
            keyed.append((date(int(m.group(2)), int(m.group(1)[1]) * 3, 1), c))
    return max(keyed)[1] if keyed else None


def quarter_value(df: pd.DataFrame, concepts: tuple[str, ...], col: str) -> float | None:
    """Valor de un concepto en la columna dada.

    El DataFrame XBRL del 10-Q tiene 'concept' como COLUMNA con prefijo
    ('us-gaap_Revenues'); se normaliza a índice sin prefijo. Las filas dimensionales
    (segmentos) se excluyen: solo totales.
    """
    if "concept" in df.columns:
        if "dimension" in df.columns:                    # verificado en vivo: columna existe
            df = df[~df["dimension"].fillna(False).astype(bool)]
        keys = df["concept"].astype(str).str.split("_", n=1).str[-1]
        df = df.set_index(keys)
    for c in concepts:
        row = _row(df, c)
        if row is not None and (v := _to_float(row.get(col))) is not None:
            return v
    return None


# --------------------------------------------------------------------------- #
# Derivados del income statement
# --------------------------------------------------------------------------- #

def subtract_aligned(a_list: list[dict], b_list: list[dict]) -> list[dict]:
    """Resta b de a alineando por periodo. Solo emite periodos presentes en ambos.

    Usado para derivar EBIT cuando no hay `OperatingIncomeLoss`
    (GrossProfit − SG&A, Pretax − InterestNet). El source queda "derived:A-B".
    """
    b_by_period = {item["period"]: item for item in b_list}
    return [
        {"value": a["value"] - b_by_period[a["period"]]["value"], "period": a["period"],
         "source": f"derived:{a.get('source')}-{b_by_period[a['period']].get('source')}"}
        for a in a_list
        if a["period"] in b_by_period
    ]


# Equity de la matriz: StockholdersEquity; si el filer solo reporta el total con minoritarios,
# esa variante (mejor que un None que cambia el método de IC). Una sola cadena para el balance
# y para balance_history (revisión final H1, #6).
EQUITY_CONCEPTS = ("StockholdersEquity",
                   "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")

# Cadena de beneficio antes de impuestos. Muchos filers (los que tienen participaciones por
# puesta en equivalencia) solo reportan la segunda variante; sin ella el tipo efectivo caía al
# 25 % por defecto sin datos (revisión final H1, #2).
PRETAX_CONCEPTS = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
)


def compute_operating_income(inc_df: pd.DataFrame) -> list[dict]:
    """Operating income (EBIT) con fallbacks múltiples (EC-21).

    Muchas small/mid caps no reportan `OperatingIncomeLoss`. Cadena:
        1. OperatingIncomeLoss
        2. GrossProfit − SG&A (retailers/services, p.ej. BBW)
        3. Pretax − InterestIncomeExpenseNonoperatingNet
        4. Pretax (proxy; sobreestima EBIT por el interés neto)
    """
    op_inc = annual_series(inc_df, ("OperatingIncomeLoss",))
    if op_inc:
        return op_inc

    gp = annual_series(inc_df, ("GrossProfit",))
    sga = annual_series(inc_df, ("SellingGeneralAndAdministrativeExpense",))
    if gp and sga:
        derived = subtract_aligned(gp, sga)
        if derived:
            return derived

    pretax = annual_series(inc_df, PRETAX_CONCEPTS)
    # InterestIncomeExpenseNonoperatingNet: positivo = ingreso neto. EBIT = Pretax − neto.
    interest = annual_series(inc_df, ("InterestIncomeExpenseNonoperatingNet",))
    if pretax and interest:
        derived = subtract_aligned(pretax, interest)
        if derived:
            return derived

    return pretax  # [] si tampoco hay pretax


# --------------------------------------------------------------------------- #
# Tipo impositivo canónico (revisión adversarial: UNA sola definición)
# --------------------------------------------------------------------------- #

def effective_tax_rate(tax: float | None, pretax: float | None) -> float:
    """Tasa efectiva del AÑO: tax/pretax con clamp [0, 0.5]; 0.25 si falta o pretax <= 0.

    El clamp evita distorsiones por DTA/DTL o años con impuesto negativo; el 0.25
    es el marginal US aproximado (federal 21 % + estatal) cuando no es calculable.
    """
    if tax is None or pretax is None or pretax <= 0:
        return 0.25
    return max(0.0, min(tax / pretax, 0.5))


def effective_tax_rate_multi(taxes: list[float], pretaxes: list[float], years: int = 3) -> float:
    """Σtax/Σpretax sobre hasta `years` ejercicios alineados por posición (index 0 = latest).

    Agregar suaviza efectos puntuales (remedición TCJA, liberación de valuation
    allowance). Clamp [0, 0.5]; 0.25 si Σpretax ≤ 0 o no hay datos.
    Canónica para FCFF, ROIC y Kd (el Bloque C la re-exporta desde valuation_math).
    """
    # zip aceptado: el llamador pasa dos series con las mismas etiquetas FY (index 0 = latest).
    pairs = [(t, p) for t, p in zip(taxes[:years], pretaxes[:years]) if t is not None and p is not None]
    total_pretax = sum(p for _, p in pairs)
    if not pairs or total_pretax <= 0:
        return 0.25
    return max(0.0, min(sum(t for t, _ in pairs) / total_pretax, 0.5))


# --------------------------------------------------------------------------- #
# Cadenas de conceptos
# --------------------------------------------------------------------------- #

REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
    "SalesRevenueNet",                                       # legacy pre-ASC 606 (EC-67: HRL)
    "RevenueFromContractWithCustomerIncludingAssessedTax",   # ASC 606 con IVA incluido
    "SalesRevenueGoodsNet",                                  # product-only (CPG/retail)
    "NetSales",                                              # algunos industriales/food
)
OCF_CONCEPTS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "NetCashProvidedByOperatingActivities", "CashGeneratedFromOperatingActivities",
)
# Asset-light puede no reportar capex: lista vacía y FCF ≈ OCF (source calculated-no-capex).
CAPEX_CONCEPTS = (
    "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalExpenditures",
    "CapitalExpendituresIncurringNoCapitalLeases", "PurchaseOfPropertyPlantAndEquipment",
    # EC-90 (PRDO, verificado en vivo): asset-light / educación reportan el capex así.
    "PaymentsForCapitalImprovements", "PaymentsToAcquireProductiveAssets",
)
BUYBACK_CONCEPTS = ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity")
DIVIDEND_CONCEPTS = ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends")
# EC-102: la línea combinada (caja + inversiones a corto, p. ej. TGT) va ANTES que "Cash", que es el
# concepto más estrecho: en raw facts puede ser una partida de las notas (TGT: 0,25 B frente a 5,49 B).
CASH_COMBINED = "CashCashEquivalentsAndShortTermInvestments"
CASH_CONCEPTS = ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents", CASH_COMBINED, "Cash")

# --- Deuda (EC-80). Política EXCLUSIVA: un total nunca se suma con sus componentes. ---
# us-gaap LongTermDebt INCLUYE la porción corriente; LongTermDebtNoncurrent no.
TOTAL_INCL_CURRENT = ("LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities")
NONCURRENT = ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable")
CURRENT_LTD = ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent")
# Exclusive: CommercialPaper is usually a component of ShortTermBorrowings → take only the first.
ST_BORROWINGS = ("ShortTermBorrowings", "CommercialPaper", "NotesPayableCurrent")
FIN_LEASE_TOTAL = ("FinanceLeaseLiability",)
# Inversiones financieras (EC-81): antes STI estaba hardcodeado a 0.
STI = ("ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent")
# LTI (T8, revisión de método): valores negociables primero; LongTermInvestments puede mezclar
# participaciones (equity method), que van aparte en EQUITY_METHOD con su propio campo.
LTI = ("MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent", "LongTermInvestments")
RESTRICTED_CASH = ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",)
# Insumos de los scores contables (bloque A, A2, ADR-043): Piotroski F, Altman Z'', Beneish M.
CURRENT_ASSETS = ("AssetsCurrent",)
RETAINED_EARNINGS = ("RetainedEarningsAccumulatedDeficit",)
PPE_NET = ("PropertyPlantAndEquipmentNet",
           "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization")
RECEIVABLES = ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent")
SGA = ("SellingGeneralAndAdministrativeExpense",)
# Derivación por año cuando la empresa no publica el agregado (A2): MSFT da S&M y G&A por separado.
# NO se deriva el beneficio bruto como ingresos − coste de ventas: verificado con CAT, la vista curada
# trae CostOfGoodsAndServicesSold = 49 M$ en FY2025 (42.767 M$ en FY2023), una partida distinta con el
# mismo concepto → margen bruto falso del 99,9 %. Mejor "n/a" explícito que un dato falso (EC-104).
SGA_PARTS = ("SellingAndMarketingExpense", "GeneralAndAdministrativeExpense")
_TOL = 0.02  # 2 % tolerance for XBRL roundings

# --- Inputs FCFF (T8, consumidos por el Bloque C) ---
INTEREST_EXPENSE = ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestAndDebtExpense")
INTEREST_INCOME = ("InvestmentIncomeInterest", "InterestIncomeOther",
                   "InvestmentIncomeInterestAndDividend", "InterestAndDividendIncomeOperating")
SBC = ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense")
DA = ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
      "DepreciationAmortizationAndAccretionNet", "Depreciation")
DILUTED_W = ("WeightedAverageNumberOfDilutedSharesOutstanding",)
BASIC_W = ("WeightedAverageNumberOfSharesOutstandingBasic",)
# Principal de leasing financiero: "capex equivalente" que el FCFF debe restar.
FIN_LEASE_PRINCIPAL = ("FinanceLeasePrincipalPayments", "RepaymentsOfLongTermCapitalLeaseObligations")
# --- Memo de balance (no entran en total_debt; el bridge decide qué hacer con cada uno) ---
OP_LEASE_TOTAL = ("OperatingLeaseLiability",)          # ASC 842: memo, NO deuda
OP_ROU_ASSET = ("OperatingLeaseRightOfUseAsset",)      # se excluye del IC operativo (T9)
EQUITY_METHOD = ("EquityMethodInvestments",)            # cross-holdings (Damodaran: se suman)
MINORITY = ("MinorityInterest",)
PREFERRED = ("PreferredStockValue",)


# --------------------------------------------------------------------------- #
# Balance: deuda, caja, identidades contables (T6)
# --------------------------------------------------------------------------- #

# Tipo del "lookup" que reciben resolve_*: una closure concepts → {value, period, source} | None,
# normalmente `lambda cs: point_value(bs_df, cs, period, facts)` fijada a UN periodo.
Lookup = Callable[[tuple[str, ...]], dict | None]


def _sum_items(items: list[dict | None], period: str) -> dict:
    """Suma de los items presentes con procedencia "derived:A+B"; "absent" si ninguno.

    Un 0.0 por defecto SIEMPRE lleva source "absent" (nunca parece un dato real).
    """
    present = [i for i in items if i]
    return {"value": sum(i["value"] for i in present), "period": period,
            "source": "derived:" + "+".join(i["source"] for i in present) if present else "absent"}


def resolve_debt(lookup: Lookup, period: str) -> dict:
    """Deuda financiera sin doble conteo (EC-80).

    us-gaap LongTermDebt INCLUYE la porción corriente; LongTermDebtNoncurrent no.
    Prioridad: noncurrent explícito > total − corriente. Corto plazo: DebtCurrent (total
    oficial) si existe; si no, borrowings (exclusivos entre sí) + porción corriente.
    Leasing financiero se suma solo si el concepto de deuda usado no lo incluye ya.

    Devuelve {long_term_debt, short_term_debt, finance_leases, flags}; las identidades
    rotas no bloquean: van a `flags` y llegan al report (data_quality_flags).
    """
    flags: list[str] = []
    total_incl, noncurrent = lookup(TOTAL_INCL_CURRENT), lookup(NONCURRENT)
    current_ltd, st_borrow, debt_current = lookup(CURRENT_LTD), lookup(ST_BORROWINGS), lookup(("DebtCurrent",))

    if noncurrent:
        lt = noncurrent
    elif total_incl:
        if current_ltd:
            cur, cur_src = current_ltd["value"], current_ltd["source"]
        elif debt_current:
            # Revisión adversarial: LongTermDebt (incluye corriente) + DebtCurrent sin LongTermDebtCurrent
            # explícito doble-contaría la porción corriente. Se infiere: DebtCurrent − borrowings CP.
            cur = max(debt_current["value"] - (st_borrow["value"] if st_borrow else 0.0), 0.0)
            cur_src = "DebtCurrent-borrowings"
            flags.append(f"current LTD inferred from DebtCurrent ({period})")
        else:
            cur, cur_src = 0.0, None
        src = f"derived:{total_incl['source']}-{cur_src}" if cur_src else total_incl["source"]
        lt = {"value": total_incl["value"] - cur, "period": period, "source": src}
    else:
        lt = {"value": 0.0, "period": period, "source": "absent"}

    # DebtCurrent es el total oficial de deuda corriente: si existe, gana a sus componentes.
    st = debt_current if debt_current else _sum_items([st_borrow, current_ltd], period)

    # Identidades contables: no bloquean, se marcan (llegan al report).
    if total_incl and noncurrent and current_ltd:
        expected = noncurrent["value"] + current_ltd["value"]
        if abs(total_incl["value"] - expected) > _TOL * max(total_incl["value"], 1.0):
            flags.append(f"{total_incl['source']}={total_incl['value']:.0f} != "
                         f"noncurrent+current={expected:.0f} ({period})")
    if debt_current and current_ltd and debt_current["value"] + 1.0 < current_ltd["value"]:
        flags.append(f"DebtCurrent < {current_ltd['source']} ({period})")

    # "...AndCapitalLeaseObligations" ya contiene los leasings financieros → no sumarlos otra vez.
    already_has_leases = "CapitalLease" in lt["source"]
    if already_has_leases:
        leases = {"value": 0.0, "period": period, "source": f"included-in:{lt['source']}"}
    else:
        leases = lookup(FIN_LEASE_TOTAL) or _sum_items(
            [lookup(("FinanceLeaseLiabilityNoncurrent",)), lookup(("FinanceLeaseLiabilityCurrent",))], period)
    return {"long_term_debt": lt, "short_term_debt": st, "finance_leases": leases, "flags": flags}


def resolve_cash(lookup: Lookup, period: str, ticker: str) -> dict:
    """Caja: conceptos estándar → con restricted cash (marcado en source) → fail loud (EC-81).

    Antes, sin concepto de caja se ponía 0.0 en silencio: el EV y el IC salían
    sobrestimados sin aviso. Ahora la ausencia es un DataUnavailableError.
    """
    cash = lookup(CASH_CONCEPTS)
    if cash:
        return cash
    restricted = lookup(RESTRICTED_CASH)
    if restricted:
        return {**restricted, "source": f"{restricted['source']} (includes restricted cash)"}
    raise DataUnavailableError(f"{ticker}: cash and equivalents missing for {period}")


def resolve_sti(lookup: Lookup, period: str, cash: dict | None) -> dict:
    """Inversiones a corto del periodo, coherentes con la fuente de la caja (EC-81, EC-102).

    Si la caja salió de la línea combinada (caja + inversiones a corto), las inversiones ya están
    dentro: se devuelven como 0 "incluidas" para que el bridge no las reste dos veces (un STI
    de las notas en raw facts se sumaría otra vez). Para EV e IC es neutro: lo que se resta es
    caja + STI, el mismo total.
    """
    if cash and cash["source"].removeprefix("facts:") == CASH_COMBINED:
        return {"value": 0.0, "period": period, "source": f"included in {CASH_COMBINED}"}
    return lookup(STI) or {"value": 0.0, "period": period, "source": "absent"}


def balance_identity_flags(lookup: Lookup, period: str, ticker: str) -> list[str]:
    """Identidades contables del balance.

    Assets == LiabilitiesAndStockholdersEquity debe ser exacta → si no, extracción corrupta: raise.
    Assets ≈ Liabilities + Equity(incl. NCI) puede romperse por mezzanine/temporary equity → flag.
    """
    assets, l_and_e = lookup(("Assets",)), lookup(("LiabilitiesAndStockholdersEquity",))
    if assets and l_and_e and abs(assets["value"] - l_and_e["value"]) > 0.01 * abs(assets["value"]):
        raise DataUnavailableError(
            f"{ticker}: balance identity broken for {period}: Assets {assets['value']:.0f} "
            f"vs L+E {l_and_e['value']:.0f}")
    liabilities = lookup(("Liabilities",))
    equity = lookup(("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"))
    if assets and liabilities and equity:
        gap = assets["value"] - liabilities["value"] - equity["value"]
        if abs(gap) > _TOL * abs(assets["value"]):
            return [f"Liabilities+Equity differs from Assets by {gap:.0f} ({period}) — mezzanine/temporary equity?"]
    return []


# --------------------------------------------------------------------------- #
# Último ejercicio perdido por edgartools (EC-100)
# --------------------------------------------------------------------------- #

_RESTORE_TOL = 0.005  # 0,5 %: misma cifra salvo redondeos/restatements menores


def restore_latest_fy_column(dfs: tuple[pd.DataFrame, ...], fy_label: str,
                             facts: FactsLookup) -> str | None:
    """Reconstruye desde raw facts la columna del último 10-K si edgartools la ha perdido.

    Por qué existe (EC-100): en los 10-K de minoristas con cierre ene/feb (HD, TGT), SEC etiqueta
    los tres ejercicios del filing con el mismo `fy` (p. ej. 2025). edgartools, para cierres que
    no son diciembre, etiqueta con ese tag → las columnas colisionan y se descarta el último año.
    Verificado en 5.59.1 y 5.61.1. Los raw facts sí lo tienen, emparejados por period_end.

    Solo actúa en el caso exacto y comprobable; en cualquier otro devuelve None y el invariante
    de build_raw falla de forma explícita:
      1. falta EXACTAMENTE un año (la última columna es fy_label − 1);
      2. raw facts tiene el revenue de fy_label;
      3. el revenue de la columna anterior coincide con el de raw facts para esa etiqueta
         (si no, la colisión pudo dejar datos de OTRO año bajo esa etiqueta: no se adivina).
    Muta los DataFrames (añade la columna 'FY YYYY' fila a fila por concepto; NaN si no hay
    fact) y devuelve un flag de procedencia para data_quality_flags.
    """
    inc_df = dfs[0]
    cols = fy_columns(inc_df)
    target, prev_col = fy_year(fy_label), (cols[0] if cols else None)
    if prev_col is None or fy_year(prev_col) != target - 1:
        return None
    prev_label = prev_col.replace(" ", "")

    # Concepto de revenue que usa la vista curada en la columna anterior (ancla de alineación).
    anchor = next((c for c in REVENUE_CONCEPTS
                   if (r := _row(inc_df, c)) is not None and _to_float(r.get(prev_col)) is not None),
                  None)
    if anchor is None or facts.annual_value(anchor, fy_label) is None:
        return None
    curated_prev = _to_float(_row(inc_df, anchor).get(prev_col))
    facts_prev = facts.annual_value(anchor, prev_label)
    if facts_prev is None or abs(curated_prev - facts_prev) > _RESTORE_TOL * abs(facts_prev):
        return None

    new_col = f"FY {target}"
    for df in dfs:
        # Un concepto por fila (índice sin prefijo, como en la vista curada); NaN si no hay fact:
        # los huecos se tratan igual que cualquier otro hueco (backfill/merge/point_value).
        df[new_col] = [facts.annual_value(str(c), fy_label) if isinstance(c, str) else None
                       for c in df.index]
        df[new_col] = pd.to_numeric(df[new_col], errors="coerce")
    log.warning("latest_fy_column_restored_from_facts", period=fy_label, anchor=anchor)
    return (f"{fy_label} column rebuilt from raw facts by period_end: edgartools dropped the "
            f"latest fiscal year (fiscal-year label collision, EC-100)")


# --------------------------------------------------------------------------- #
# Acciones desde el último 10-K, multiclase incluidas (EC-101)
# --------------------------------------------------------------------------- #

_CLASS_AXIS = "dim_us-gaap_StatementClassOfStockAxis"
_COVER_SHARES = "dei:EntityCommonStockSharesOutstanding"
_DILUTED_SHARES = "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"
_AS_CONVERTED_MIN = 0.95  # una cifra "as-converted" incluye todas las clases: ≥ ~suma de portada


def share_facts_from_xbrl_df(df: pd.DataFrame, period_end: date
                             ) -> tuple[list[tuple[float, str | None]], list[tuple[float, str | None]]]:
    """Extrae del XBRL del 10-K (XBRL.facts.to_dataframe()) los recuentos de acciones.

    Devuelve (cover, diluted), listas de (valor, miembro de clase | None):
      - cover: dei:EntityCommonStockSharesOutstanding de la portada (fecha más reciente);
      - diluted: diluidas medias del ejercicio del 10-K (period_end exacto, duración anual).
    Solo se aceptan hechos sin dimensión o con la única dimensión de clase de acción: cualquier
    otro eje (co-registrantes, segmentos) describe otra cosa.
    Por qué del 10-K y no de companyfacts: companyfacts descarta los hechos con dimensión, y en
    las multiclase el único dato de portada sin dimensión es de antes de separar por clases
    (V: enero de 2010) → recuentos caducados de hace 15 años (EC-101).
    """
    dim_cols = [c for c in df.columns if c.startswith("dim_")]
    other_dims = [c for c in dim_cols if c != _CLASS_AXIS]

    def clean(sub: pd.DataFrame) -> pd.DataFrame:
        if other_dims:
            sub = sub[sub[other_dims].isna().all(axis=1)]
        return sub

    def member(r: pd.Series) -> str | None:
        v = r.get(_CLASS_AXIS)
        return v if isinstance(v, str) else None

    cover_df = clean(df[df["concept"] == _COVER_SHARES])
    if not cover_df.empty:
        last = cover_df["period_instant"].astype(str).max()      # fecha de portada más reciente
        cover_df = cover_df[cover_df["period_instant"].astype(str) == last]
    cover = [(v, member(r)) for _, r in cover_df.iterrows() if (v := _to_float(r["value"])) is not None]

    dil_df = clean(df[df["concept"] == _DILUTED_SHARES])
    diluted = []
    for _, r in dil_df.iterrows():
        try:
            start, end = date.fromisoformat(str(r["period_start"])), date.fromisoformat(str(r["period_end"]))
        except ValueError:
            continue
        # Solo el ejercicio del 10-K: mismo cierre y duración anual (>300 d, como edgartools).
        if end == period_end and (end - start).days > 300 and (v := _to_float(r["value"])) is not None:
            diluted.append((v, member(r)))
    return cover, diluted


def resolve_filing_shares(cover: list[tuple[float, str | None]],
                          diluted: list[tuple[float, str | None]]) -> dict[str, Any]:
    """Decide el recuento de acciones a partir de los hechos del 10-K (puro).

    - Una clase (portada sin dimensión): ese recuento; la dilución la aplica después
      resolve_share_count con el ratio diluidas/básicas, como siempre.
    - Varias clases: las clases pueden convertirse entre sí con ratios distintos de 1 (V: B y C
      se convierten a A), así que sumar la portada no es el número económico. Se usa la cifra
      diluida "as-converted" que publica la propia empresa: primero la total sin dimensión
      (GOOGL, MA); si no existe, la mayor por clase (V: la clase A as-converted). Para distinguir
      una cifra as-converted de la de una sola clase se exige ≥ 95 % de la suma de portada.
      Va a `as_converted_diluted` (ya es diluida → shares_outstanding None, no se diluye dos veces).
    - Varias clases sin cifra as-converted: suma de portada, con aviso explícito.
    Devuelve {"shares_outstanding", "as_converted_diluted", "flag"}.
    """
    total = [v for v, m in cover if m is None]
    by_class = [(v, m) for v, m in cover if m is not None]
    if total:
        return {"shares_outstanding": total[0], "as_converted_diluted": None, "flag": None}
    if not by_class:
        return {"shares_outstanding": None, "as_converted_diluted": None, "flag": None}

    cover_sum = sum(v for v, _ in by_class)
    candidates = [v for v, m in diluted if m is None] + sorted((v for v, m in diluted if m), reverse=True)
    as_conv = next((v for v in candidates if v >= _AS_CONVERTED_MIN * cover_sum), None)
    n = len(by_class)
    if as_conv is not None:
        return {"shares_outstanding": None, "as_converted_diluted": as_conv,
                "flag": (f"multi-class equity ({n} classes, cover total {cover_sum:,.0f}): per-share "
                         f"values use the as-converted diluted weighted average from the 10-K "
                         f"({as_conv:,.0f})")}
    return {"shares_outstanding": cover_sum, "as_converted_diluted": None,
            "flag": (f"multi-class equity ({n} classes): share count is the sum of cover-page classes "
                     f"({cover_sum:,.0f}); conversion ratios not applied")}


def _with_as_converted(series: list[dict], as_converted: float | None, fy_label: str) -> list[dict]:
    """Añade la diluida as-converted del 10-K si la serie de companyfacts no tiene el año.

    Caso V: companyfacts no trae diluidas (solo existen por clase) → sin esto, la valoración caía
    a market_cap/precio. Si companyfacts ya tiene el año (MA, GOOGL) se respeta su valor.
    """
    if as_converted is None or any(i["period"] == fy_label for i in series):
        return series
    return [{"value": as_converted, "period": fy_label,
             "source": "10-K:WeightedAverageNumberOfDilutedSharesOutstanding (as-converted)"}] + series


# --------------------------------------------------------------------------- #
# Ensamblado del raw
# --------------------------------------------------------------------------- #

def build_raw(*, ticker: str, cik: str, period_end: date, inc_df: pd.DataFrame,
              bs_df: pd.DataFrame, cf_df: pd.DataFrame, facts: FactsLookup,
              shares_outstanding: float | None, as_converted_diluted: float | None = None,
              share_flags: list[str] | None = None) -> dict[str, Any]:
    """Ensambla el dict raw (contrato de parse_financials) desde DataFrames puros.

    `shares_outstanding`, `as_converted_diluted` y `share_flags` vienen de resolve_filing_shares
    (último 10-K, EC-101).

    Invariante: la etiqueta del 10-K == la columna más reciente de los estados;
    si difieren mezclaríamos años → DataUnavailableError (fail loud, EC-87).
    Única excepción comprobable: el último ejercicio perdido por edgartools (EC-100).
    """
    fy_label = fy_label_from_period_end(period_end)
    cols = fy_columns(inc_df)
    if not cols:
        raise DataUnavailableError(f"{ticker}: income statement has no FY columns")
    restore_flags: list[str] = []
    if cols[0].replace(" ", "") != fy_label:
        # Copias: el adaptador añade una columna y no debe mutar los DataFrames del llamador.
        inc_df, bs_df, cf_df = inc_df.copy(), bs_df.copy(), cf_df.copy()
        if (flag := restore_latest_fy_column((inc_df, bs_df, cf_df), fy_label, facts)) is not None:
            restore_flags.append(flag)
            cols = fy_columns(inc_df)
    latest = cols[0].replace(" ", "")
    if latest != fy_label:
        raise DataUnavailableError(
            f"{ticker}: latest 10-K period {period_end} → {fy_label} but statements' "
            f"latest column is {latest} (period misalignment)")

    # A6 (ADR-043): la cadena de conceptos de ingresos se fusiona AÑO A AÑO (como EC-90 para OCF/capex).
    # Antes se tomaba el primer concepto con algún dato: PFE (RevenueFromContract… desde 2018,
    # Revenues antes) perdía FY2016–FY2017. Medido en las 33 empresas del diagnóstico: solo cambia PFE
    # (gana esos dos años); ningún valor existente varía.
    col_labels = [c.replace(" ", "") for c in fy_columns(inc_df)]
    revenue = prefer_total_revenue(merge_annual(REVENUE_CONCEPTS, (inc_df,), facts, col_labels), facts)
    if not revenue:
        raise DataUnavailableError(f"{ticker}: revenue concept not found in income statement")

    fill = lambda series: backfill_gaps(series, facts)  # noqa: E731 — closure sobre facts

    # Closure de periodo: todos los puntos del balance se leen del MISMO fy_label.
    get = lambda concepts: point_value(bs_df, concepts, fy_label, facts)  # noqa: E731

    assets = get(("Assets",))
    if assets is None:
        raise DataUnavailableError(f"{ticker}: Assets missing for {fy_label}")

    # --- Deuda, caja e identidades (T6): todo del MISMO periodo fy_label ---
    debt = resolve_debt(get, fy_label)
    flags = restore_flags + list(share_flags or []) + debt["flags"] + balance_identity_flags(get, fy_label, ticker)
    cash = resolve_cash(get, fy_label, ticker)

    # Leasing operativo (ASC 842): total si existe; si no, noncurrent + current. Memo, no deuda.
    op_lease = get(OP_LEASE_TOTAL)
    if op_lease is None and get(("OperatingLeaseLiabilityNoncurrent",)):
        op_lease = _sum_items([get(("OperatingLeaseLiabilityNoncurrent",)),
                               get(("OperatingLeaseLiabilityCurrent",))], fy_label)

    # Rejilla de etiquetas FY del revenue: extract_annual consulta raw facts en ESTOS años,
    # para que los inputs FCFF se puedan alinear por periodo con OCF/capex.
    # EC-88/EC-90: la rejilla se toma DESPUÉS de rellenar huecos del revenue desde facts; si no,
    # un año que solo existe en facts (BBW FY2025) aparecía en revenue pero no en OCF/capex/FCF.
    revenue = fill(revenue)
    labels = [i["period"] for i in revenue]
    dfs = (inc_df, cf_df)
    if flags:
        log.warning("balance_data_quality_flags", ticker=ticker, flags=flags)

    # --- balance_history (T9): un balance por columna FY → IC por año, series de deuda/caja/equity ---
    # Cada año usa las MISMAS reglas que el último (resolve_debt, caja solo-caja, STI aparte).
    val = lambda item: item["value"] if item else None  # noqa: E731
    history = []
    # EC-88: misma rejilla de años que las series de resultados (incluye años rellenados desde
    # facts), no solo las columnas curadas del balance. point_value ya cae a facts por concepto.
    balance_years = sorted({c.replace(" ", "") for c in fy_columns(bs_df)} | set(labels),
                           key=fy_year, reverse=True)[:10]
    for p in balance_years:
        g = lambda c, p=p: point_value(bs_df, c, p, facts)  # noqa: E731 — closure de periodo
        d = resolve_debt(g, p)
        history.append({
            "period": p, "total_assets": val(g(("Assets",))),
            "stockholders_equity": val(g(EQUITY_CONCEPTS)),
            "total_debt": d["long_term_debt"]["value"] + d["short_term_debt"]["value"] + d["finance_leases"]["value"],
            "short_term_debt": d["short_term_debt"]["value"],
            # caja de cada año: no lanza (años antiguos pueden no tenerla) → None y el año se omite del IC
            "cash": val(cash_p := g(CASH_CONCEPTS + RESTRICTED_CASH)),
            "short_term_investments": val(resolve_sti(g, p, cash_p)),
            "current_liabilities": val(g(("LiabilitiesCurrent",))),
            "operating_rou_asset": val(g(OP_ROU_ASSET)),
            "long_term_investments": val(g(LTI)),
            # Scores contables (A2): None si el concepto no existe ese año (nunca 0 implícito).
            "current_assets": val(g(CURRENT_ASSETS)),
            "retained_earnings": val(g(RETAINED_EARNINGS)),
            "ppe_net": val(g(PPE_NET)),
            "receivables": val(g(RECEIVABLES)),
        })

    return {
        "ticker": ticker.upper(), "cik": cik, "as_of_date": period_end.isoformat(),
        "source_filing": f"10-K {fy_label}",
        # Cada serie anual pasa por backfill_gaps (EC-88): huecos interiores desde raw facts.
        "annual": {
            "revenue": revenue,  # ya rellenado arriba (rejilla de etiquetas)
            "gross_profit": fill(annual_series(inc_df, ("GrossProfit",))),
            "operating_income": fill(compute_operating_income(inc_df)),
            "net_income": resolve_net_income(inc_df, facts, labels),  # EC-103: año a año, ProfitLoss
            "operating_cash_flow": merge_annual(OCF_CONCEPTS, (cf_df,), facts, labels),  # EC-90: año a año
            "capex": merge_annual(CAPEX_CONCEPTS, (cf_df,), facts, labels),  # EC-90: año a año
            # Para ROIC: impuesto + pretax permiten el tipo efectivo por año.
            "income_tax": fill(annual_series(inc_df, ("IncomeTaxExpenseBenefit",))),
            "pretax_income": fill(annual_series(inc_df, PRETAX_CONCEPTS)),
            # Capital returns (ADR-023): negativos en XBRL; parse_financials toma magnitud.
            "buybacks": merge_annual(BUYBACK_CONCEPTS, (cf_df,), facts, labels),  # EC-90: año a año
            "dividends": merge_annual(DIVIDEND_CONCEPTS, (cf_df,), facts, labels),  # EC-90: año a año
            # --- Inputs FCFF (T8): misma rejilla de etiquetas que el revenue ---
            "interest_expense": extract_annual(INTEREST_EXPENSE, dfs, facts, labels),
            "interest_income": extract_annual(INTEREST_INCOME, dfs, facts, labels),
            "sbc": extract_annual(SBC, (cf_df, inc_df), facts, labels),
            "depreciation_amortization": extract_annual(DA, (cf_df, inc_df), facts, labels),
            "diluted_shares": _with_as_converted(extract_annual(DILUTED_W, (inc_df,), facts, labels),
                                                 as_converted_diluted, fy_label),
            "basic_shares_weighted": extract_annual(BASIC_W, (inc_df,), facts, labels),
            "sga": derive_sga(extract_annual(SGA, (inc_df,), facts, labels), inc_df, facts, labels),  # A2: Beneish
            "finance_lease_principal_payments": extract_annual(FIN_LEASE_PRINCIPAL, (cf_df,), facts, labels),
        },
        "balance_sheet": {
            "total_assets": assets,
            "long_term_debt": debt["long_term_debt"],
            "short_term_debt": debt["short_term_debt"],
            "finance_leases": debt["finance_leases"],
            # cash = SOLO caja (EC-81); STI en su propio campo → el bridge la resta UNA vez.
            "cash_and_equivalents": cash,
            "short_term_investments": resolve_sti(get, fy_label, cash),
            "long_term_investments": get(LTI),
            "current_liabilities": get(("LiabilitiesCurrent",)),
            "stockholders_equity": get(EQUITY_CONCEPTS),
            # --- Memo (T8): visibles, pero NO dentro de total_debt ---
            "operating_lease_liabilities": op_lease,
            "operating_rou_asset": get(OP_ROU_ASSET),
            "equity_method_investments": get(EQUITY_METHOD),
            "minority_interest": get(MINORITY),
            "preferred_stock": get(PREFERRED),
        },
        # Identidades contables rotas / inferencias: llegan al FinancialsReport y al report.
        "data_quality_flags": flags,
        "balance_history": history,
        "shares": {"shares_outstanding": {
            "value": float(shares_outstanding) if shares_outstanding is not None else None,
            "period": fy_label, "source": "dei:EntityCommonStockSharesOutstanding"}},
    }
