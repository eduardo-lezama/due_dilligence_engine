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
    concept = series[0]["source"]
    if ":" in concept:          # "facts:X" / "derived:..." no son conceptos consultables → no rellenar
        return series
    by_year = {fy_year(i["period"]): i for i in series}
    hi, lo = max(by_year), min(by_year)
    for y in range(hi - 1, lo, -1):
        if y not in by_year:
            v = facts.annual_value(concept, f"FY{y}")
            if v is not None:
                by_year[y] = {"value": v, "period": f"FY{y}", "source": f"facts:{concept}"}
    return [by_year[y] for y in sorted(by_year, reverse=True)]


def extract_annual(concepts: tuple[str, ...], dfs: tuple[pd.DataFrame, ...], facts: FactsLookup,
                   labels: list[str], n: int = 10) -> list[dict]:
    """Serie anual para conceptos que la vista curada puede omitir (interest, SBC, D&A, shares).

    1) primer DataFrame curado que la tenga (+ backfill de huecos);
    2) si ninguno: facts en bruto para cada etiqueta de `labels` (las del revenue), de modo que
       la serie comparte etiquetas con el resto y el cálculo de FCFF puede alinear por periodo.
    """
    for df in dfs:
        if series := annual_series(df, concepts, n):
            return backfill_gaps(series, facts)
    for concept in concepts:
        out = [{"value": v, "period": lb, "source": f"facts:{concept}"}
               for lb in labels[:n] if (v := facts.annual_value(concept, lb)) is not None]
        if out:
            return out
    return []


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

    fill = lambda series: backfill_gaps(series, facts)  # noqa: E731 — closure sobre facts

    # Closure de periodo: todos los puntos del balance se leen del MISMO fy_label.
    get = lambda concepts: point_value(bs_df, concepts, fy_label, facts)  # noqa: E731

    assets = get(("Assets",))
    if assets is None:
        raise DataUnavailableError(f"{ticker}: Assets missing for {fy_label}")

    # --- Deuda, caja e identidades (T6): todo del MISMO periodo fy_label ---
    debt = resolve_debt(get, fy_label)
    flags = debt["flags"] + balance_identity_flags(get, fy_label, ticker)
    cash = resolve_cash(get, fy_label, ticker)

    # Leasing operativo (ASC 842): total si existe; si no, noncurrent + current. Memo, no deuda.
    op_lease = get(OP_LEASE_TOTAL)
    if op_lease is None and get(("OperatingLeaseLiabilityNoncurrent",)):
        op_lease = _sum_items([get(("OperatingLeaseLiabilityNoncurrent",)),
                               get(("OperatingLeaseLiabilityCurrent",))], fy_label)

    # Rejilla de etiquetas FY del revenue: extract_annual consulta raw facts en ESTOS años,
    # para que los inputs FCFF se puedan alinear por periodo con OCF/capex.
    labels = [i["period"] for i in revenue]
    dfs = (inc_df, cf_df)
    if flags:
        log.warning("balance_data_quality_flags", ticker=ticker, flags=flags)

    return {
        "ticker": ticker.upper(), "cik": cik, "as_of_date": period_end.isoformat(),
        "source_filing": f"10-K {fy_label}",
        # Cada serie anual pasa por backfill_gaps (EC-88): huecos interiores desde raw facts.
        "annual": {
            "revenue": fill(revenue),
            "gross_profit": fill(annual_series(inc_df, ("GrossProfit",))),
            "operating_income": fill(compute_operating_income(inc_df)),
            "net_income": fill(annual_series(inc_df, ("NetIncomeLoss",))),
            "operating_cash_flow": fill(annual_series(cf_df, OCF_CONCEPTS)),
            "capex": fill(annual_series(cf_df, CAPEX_CONCEPTS)),
            # Para ROIC: impuesto + pretax permiten el tipo efectivo por año.
            "income_tax": fill(annual_series(inc_df, ("IncomeTaxExpenseBenefit",))),
            "pretax_income": fill(annual_series(inc_df, (PRETAX_CONCEPT,))),
            # Capital returns (ADR-023): negativos en XBRL; parse_financials toma magnitud.
            "buybacks": fill(annual_series(cf_df, BUYBACK_CONCEPTS)),
            "dividends": fill(annual_series(cf_df, DIVIDEND_CONCEPTS)),
            # --- Inputs FCFF (T8): misma rejilla de etiquetas que el revenue ---
            "interest_expense": extract_annual(INTEREST_EXPENSE, dfs, facts, labels),
            "interest_income": extract_annual(INTEREST_INCOME, dfs, facts, labels),
            "sbc": extract_annual(SBC, (cf_df, inc_df), facts, labels),
            "depreciation_amortization": extract_annual(DA, (cf_df, inc_df), facts, labels),
            "diluted_shares": extract_annual(DILUTED_W, (inc_df,), facts, labels),
            "basic_shares_weighted": extract_annual(BASIC_W, (inc_df,), facts, labels),
            "finance_lease_principal_payments": extract_annual(FIN_LEASE_PRINCIPAL, (cf_df,), facts, labels),
        },
        "balance_sheet": {
            "total_assets": assets,
            "long_term_debt": debt["long_term_debt"],
            "short_term_debt": debt["short_term_debt"],
            "finance_leases": debt["finance_leases"],
            # cash = SOLO caja (EC-81); STI en su propio campo → el bridge la resta UNA vez.
            "cash_and_equivalents": cash,
            "short_term_investments": get(STI) or {"value": 0.0, "period": fy_label, "source": "absent"},
            "long_term_investments": get(LTI),
            "current_liabilities": get(("LiabilitiesCurrent",)),
            "stockholders_equity": get(("StockholdersEquity",)),
            # --- Memo (T8): visibles, pero NO dentro de total_debt ---
            "operating_lease_liabilities": op_lease,
            "operating_rou_asset": get(OP_ROU_ASSET),
            "equity_method_investments": get(EQUITY_METHOD),
            "minority_interest": get(MINORITY),
            "preferred_stock": get(PREFERRED),
        },
        # Identidades contables rotas / inferencias: llegan al FinancialsReport y al report.
        "data_quality_flags": flags,
        "shares": {"shares_outstanding": {
            "value": float(shares_outstanding) if shares_outstanding is not None else None,
            "period": fy_label, "source": "dei:EntityCommonStockSharesOutstanding"}},
    }
