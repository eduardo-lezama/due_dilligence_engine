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


PRETAX_CONCEPT = ("IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                  "ExtraordinaryItemsNoncontrollingInterest")


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

    pretax = annual_series(inc_df, (PRETAX_CONCEPT,))
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
)
BUYBACK_CONCEPTS = ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity")
DIVIDEND_CONCEPTS = ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends")
CASH_CONCEPTS = ("CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents", "Cash")

# Cadenas de deuda heredadas de edgar.py (T5 conserva la lógica; T6 la sustituye
# por resolve_debt, que corrige el doble conteo — EC-80).
_LEGACY_LT_DEBT = (
    "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "LongTermNotesPayable",
)
_LEGACY_ST_DEBT = (
    "ShortTermBorrowings", "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "NotesPayableCurrent", "DebtCurrent",
)


# --------------------------------------------------------------------------- #
# Ensamblado del raw
# --------------------------------------------------------------------------- #

def build_raw(*, ticker: str, cik: str, period_end: date, inc_df: pd.DataFrame,
              bs_df: pd.DataFrame, cf_df: pd.DataFrame, facts: FactsLookup,
              shares_outstanding: float | None) -> dict[str, Any]:
    """Ensambla el dict raw (contrato de parse_financials) desde DataFrames puros.

    Invariante: la etiqueta del 10-K == la columna más reciente de los estados;
    si difieren mezclaríamos años → DataUnavailableError (fail loud, EC-87).
    """
    fy_label = fy_label_from_period_end(period_end)
    cols = fy_columns(inc_df)
    if not cols:
        raise DataUnavailableError(f"{ticker}: income statement has no FY columns")
    latest = cols[0].replace(" ", "")
    if latest != fy_label:
        raise DataUnavailableError(
            f"{ticker}: latest 10-K period {period_end} → {fy_label} but statements' "
            f"latest column is {latest} (period misalignment)")

    revenue = annual_series(inc_df, REVENUE_CONCEPTS)
    if not revenue:
        raise DataUnavailableError(f"{ticker}: revenue concept not found in income statement")

    # Closure de periodo: todos los puntos del balance se leen del MISMO fy_label.
    get = lambda concepts: point_value(bs_df, concepts, fy_label, facts)  # noqa: E731

    assets = get(("Assets",))
    if assets is None:
        raise DataUnavailableError(f"{ticker}: Assets missing for {fy_label}")

    # --- Deuda y caja: lógica heredada (T6 la sustituye por resolve_debt/resolve_cash) ---
    lt_debt = get(_LEGACY_LT_DEBT) or {"value": 0.0, "period": fy_label, "source": "absent"}
    st_parts = [get((c,)) for c in _LEGACY_ST_DEBT]
    st_present = [p for p in st_parts if p]
    st_debt = {"value": sum(p["value"] for p in st_present), "period": fy_label,
               "source": ("derived:" + "+".join(p["source"] for p in st_present)) if st_present else "absent"}
    cash = get(CASH_CONCEPTS) or {"value": 0.0, "period": fy_label, "source": "absent"}

    return {
        "ticker": ticker.upper(), "cik": cik, "as_of_date": period_end.isoformat(),
        "source_filing": f"10-K {fy_label}",
        "annual": {
            "revenue": revenue,
            "gross_profit": annual_series(inc_df, ("GrossProfit",)),
            "operating_income": compute_operating_income(inc_df),
            "net_income": annual_series(inc_df, ("NetIncomeLoss",)),
            "operating_cash_flow": annual_series(cf_df, OCF_CONCEPTS),
            "capex": annual_series(cf_df, CAPEX_CONCEPTS),
            # Para ROIC: impuesto + pretax permiten el tipo efectivo por año.
            "income_tax": annual_series(inc_df, ("IncomeTaxExpenseBenefit",)),
            "pretax_income": annual_series(inc_df, (PRETAX_CONCEPT,)),
            # Capital returns (ADR-023): negativos en XBRL; parse_financials toma magnitud.
            "buybacks": annual_series(cf_df, BUYBACK_CONCEPTS),
            "dividends": annual_series(cf_df, DIVIDEND_CONCEPTS),
        },
        "balance_sheet": {
            "total_assets": assets,
            "long_term_debt": lt_debt,
            "short_term_debt": st_debt,
            "cash_and_equivalents": cash,
            # STI aún no se extrae (T6 lo hace); 0 explícito con source "absent".
            "short_term_investments": {"value": 0.0, "period": fy_label, "source": "absent"},
            "stockholders_equity": get(("StockholdersEquity",)),
        },
        "shares": {"shares_outstanding": {
            "value": float(shares_outstanding) if shares_outstanding is not None else None,
            "period": fy_label, "source": "dei:EntityCommonStockSharesOutstanding"}},
    }
