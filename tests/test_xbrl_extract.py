"""
Tests de xbrl_extract — la lógica pura de extracción XBRL (sin red).

Qué cubren: etiquetas FY (misma regla que edgartools), selección de periodo exacto
(EC-78), raw facts emparejados por period_end, invariante 10-K == última columna
(EC-87), resolución de deuda/caja/identidades (EC-80/81), huecos y trimestres
(EC-84/88), extracciones FCFF y tipo impositivo canónico.
Todo con DataFrames sintéticos que imitan MultiPeriodStatement.to_dataframe().
"""
import math
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from due_diligence.tools import xbrl_extract as xe

NaN = math.nan


def make_df(rows: dict[str, dict[str, float]]) -> pd.DataFrame:
    """DataFrame con la forma de MultiPeriodStatement.to_dataframe() (edgartools 5.30):
    índice = concepto sin prefijo, columnas metadata + 'FY YYYY'."""
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "concept"
    df.insert(0, "label", df.index)
    df.insert(1, "is_total", False)
    return df


class FakeFacts:
    """FactsLookup en memoria: {(concepto, 'FY2025'): valor}."""

    def __init__(self, data: dict[tuple[str, str], float] | None = None):
        self.data = data or {}
        self.calls: list[tuple[str, str]] = []

    def annual_value(self, concept: str, period: str) -> float | None:
        self.calls.append((concept, period))
        return self.data.get((concept, period))


# --------------------------------------------------------------------------- #
# Task 5 — etiquetas FY, periodo exacto, facts por period_end, build_raw
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("end,label", [
    (date(2025, 9, 27), "FY2025"),   # AAPL
    (date(2026, 1, 31), "FY2026"),   # BBW: cierre 31-ene (no 'early January')
    (date(2023, 1, 1), "FY2022"),    # 52/53 semanas: early-January → año anterior
])
def test_fy_label_from_period_end_mirrors_edgartools(end, label):
    assert xe.fy_label_from_period_end(end) == label


def test_fy_columns_numeric_desc_ignores_metadata():
    df = make_df({"Revenues": {"FY 2023": 1.0, "FY 2025": 3.0, "FY 2024": 2.0}})
    assert xe.fy_columns(df) == ["FY 2025", "FY 2024", "FY 2023"]


def test_annual_series_skips_nan_and_carries_true_labels_and_source():
    df = make_df({"Revenues": {"FY 2025": 10.0, "FY 2024": NaN, "FY 2023": 8.0}})
    out = xe.annual_series(df, ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"))
    assert out == [
        {"value": 10.0, "period": "FY2025", "source": "Revenues"},
        {"value": 8.0, "period": "FY2023", "source": "Revenues"},
    ]


def test_point_value_respects_period_instead_of_returning_stale_year():
    # Bug viejo: _get_point devolvía 5.0 (FY2024) etiquetado como FY2025.
    df = make_df({"Assets": {"FY 2025": NaN, "FY 2024": 5.0}})
    assert xe.point_value(df, ("Assets",), "FY2025") is None


def test_point_value_falls_back_to_raw_facts_with_provenance():
    df = make_df({"Assets": {"FY 2025": NaN}})
    facts = FakeFacts({("Assets", "FY2025"): 7.0})
    assert xe.point_value(df, ("Assets",), "FY2025", facts) == {
        "value": 7.0, "period": "FY2025", "source": "facts:Assets"}


def test_edgar_facts_lookup_matches_by_period_end_not_fiscal_year():
    f = lambda **kw: SimpleNamespace(**{"dimensions": None, "fiscal_period": "FY",  # noqa: E731
                                         "form_type": "10-K", "period_start": None, **kw})
    raw = SimpleNamespace(get_all_facts=lambda: [
        f(concept="us-gaap:Assets", numeric_value=1.0, period_end=date(2024, 9, 28),
          fiscal_year=2025, filing_date=date(2025, 10, 31)),
        f(concept="us-gaap:Assets", numeric_value=2.0, period_end=date(2025, 9, 27),
          fiscal_year=2025, filing_date=date(2025, 10, 31)),
        f(concept="us-gaap:Revenues", numeric_value=9.0, period_end=date(2025, 9, 27),
          period_start=date(2025, 6, 29), fiscal_year=2025, filing_date=date(2025, 10, 31)),
    ])
    lk = xe.EdgarFactsLookup(raw)
    assert lk.annual_value("Assets", "FY2024") == 1.0
    assert lk.annual_value("Assets", "FY2025") == 2.0
    assert lk.annual_value("Revenues", "FY2025") is None   # duración 90d ≠ anual


# --------------------------------------------------------------------------- #
# F0 — minoristas con cierre ene/feb: edgartools pierde el último ejercicio (EC-100)
# --------------------------------------------------------------------------- #

def _hd_like_facts(overrides=None):
    """Facts por period_end de un minorista HD-like: el FY2026 existe en raw facts."""
    data = {("Revenues", "FY2026"): 164.0, ("OperatingIncomeLoss", "FY2026"): 21.0,
            ("NetIncomeLoss", "FY2026"): 14.0, ("Assets", "FY2026"): 520.0,
            ("LiabilitiesAndStockholdersEquity", "FY2026"): 520.0,
            ("CashAndCashEquivalentsAtCarryingValue", "FY2026"): 55.0,
            ("StockholdersEquity", "FY2026"): 210.0,
            ("NetCashProvidedByUsedInOperatingActivities", "FY2026"): 33.0,
            ("Revenues", "FY2025"): 100.0}
    data.update(overrides or {})
    return FakeFacts(data)


def test_build_raw_restores_latest_fy_column_lost_by_edgartools():
    """HD real: 10-K con cierre 2026-02-01 pero los estados acaban en FY2025 (colisión de
    etiquetas en edgartools). La columna FY2026 se reconstruye desde raw facts por period_end."""
    inc, bs, cf = _min_frames()   # columnas FY2025, FY2024
    raw = xe.build_raw(ticker="hd", cik="1", period_end=date(2026, 2, 1), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=_hd_like_facts(), shares_outstanding=10.0)
    assert raw["source_filing"] == "10-K FY2026"
    assert [(i["period"], i["value"]) for i in raw["annual"]["revenue"]][:2] == [
        ("FY2026", 164.0), ("FY2025", 100.0)]
    assert raw["annual"]["operating_income"][0] == {"value": 21.0, "period": "FY2026",
                                                    "source": "OperatingIncomeLoss"}
    assert raw["balance_sheet"]["total_assets"]["value"] == 520.0
    # Procedencia: queda constancia de que la columna no vino de la vista curada.
    assert any("FY2026" in f and "raw facts" in f for f in raw["data_quality_flags"])


def test_build_raw_restore_requires_previous_column_to_match_facts():
    """Si la columna FY2025 de los estados no coincide con el FY2025 de raw facts, la colisión
    pudo dejar datos de otro año bajo esa etiqueta → no se adivina, se falla."""
    inc, bs, cf = _min_frames()
    with pytest.raises(xe.DataUnavailableError, match="misalignment"):
        xe.build_raw(ticker="hd", cik="1", period_end=date(2026, 2, 1), inc_df=inc, bs_df=bs,
                     cf_df=cf, facts=_hd_like_facts({("Revenues", "FY2025"): 164.0}),
                     shares_outstanding=10.0)


def test_build_raw_does_not_restore_when_more_than_one_year_missing():
    inc, bs, cf = _min_frames(("FY 2024", "FY 2023"))
    with pytest.raises(xe.DataUnavailableError, match="misalignment"):
        xe.build_raw(ticker="hd", cik="1", period_end=date(2026, 2, 1), inc_df=inc, bs_df=bs,
                     cf_df=cf, facts=_hd_like_facts(), shares_outstanding=10.0)


def test_build_raw_does_not_restore_without_latest_revenue_in_facts():
    inc, bs, cf = _min_frames()
    with pytest.raises(xe.DataUnavailableError, match="misalignment"):
        xe.build_raw(ticker="hd", cik="1", period_end=date(2026, 2, 1), inc_df=inc, bs_df=bs,
                     cf_df=cf, facts=_hd_like_facts({("Revenues", "FY2026"): None}),
                     shares_outstanding=10.0)


def _min_frames(label_cols=("FY 2025", "FY 2024")):
    inc = make_df({"Revenues": {c: 100.0 for c in label_cols},
                   "OperatingIncomeLoss": {c: 20.0 for c in label_cols},
                   "NetIncomeLoss": {c: 15.0 for c in label_cols}})
    bs = make_df({"Assets": {c: 500.0 for c in label_cols},
                  "LiabilitiesAndStockholdersEquity": {c: 500.0 for c in label_cols},
                  "CashAndCashEquivalentsAtCarryingValue": {c: 50.0 for c in label_cols},
                  "StockholdersEquity": {c: 200.0 for c in label_cols}})
    cf = make_df({"NetCashProvidedByUsedInOperatingActivities": {c: 30.0 for c in label_cols}})
    return inc, bs, cf


def test_build_raw_labels_from_10k_period_end():
    inc, bs, cf = _min_frames()
    raw = xe.build_raw(ticker="x", cik="0000000001", period_end=date(2025, 9, 27),
                       inc_df=inc, bs_df=bs, cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    assert raw["source_filing"] == "10-K FY2025"
    assert raw["balance_sheet"]["total_assets"]["period"] == "FY2025"
    assert raw["annual"]["revenue"][0]["period"] == "FY2025"


def test_build_raw_fails_loud_when_statements_latest_column_differs_from_10k():
    inc, bs, cf = _min_frames(("FY 2024", "FY 2023"))
    with pytest.raises(xe.DataUnavailableError, match="FY2025"):
        xe.build_raw(ticker="x", cik="1", period_end=date(2025, 9, 27), inc_df=inc,
                     bs_df=bs, cf_df=cf, facts=FakeFacts(), shares_outstanding=None)


# --------------------------------------------------------------------------- #
# Tipo impositivo canónico (ruling R1: nace en T5; tests de T9/T10)
# --------------------------------------------------------------------------- #

def test_effective_tax_rate_per_year_rules():
    assert xe.effective_tax_rate(21.0, 100.0) == 0.21
    assert xe.effective_tax_rate(80.0, 100.0) == 0.5        # clamp
    assert xe.effective_tax_rate(5.0, -10.0) == 0.25        # pretax <= 0 → default
    assert xe.effective_tax_rate(None, 100.0) == 0.25


def test_effective_tax_rate_multi_aggregates_three_years():
    # Σtax/Σpretax = (40+15+30)/(200+150+100) = 85/450 = 0.1889 — suaviza un año con crédito fiscal
    assert xe.effective_tax_rate_multi([40.0, 15.0, 30.0], [200.0, 150.0, 100.0]) == pytest.approx(85 / 450)


def test_effective_tax_rate_multi_prdo_like_interest_income_not_a_tax():
    # EBIT 120, intereses cobrados 31.5 → pretax 151.5, tax 37.875: t = 0.25 (la fórmula NI/EBIT daría 5 %)
    assert xe.effective_tax_rate_multi([37.875], [151.5]) == pytest.approx(0.25)


def test_effective_tax_rate_multi_defaults_and_clamp():
    assert xe.effective_tax_rate_multi([], []) == pytest.approx(0.25)
    assert xe.effective_tax_rate_multi([10.0], [-5.0]) == pytest.approx(0.25)     # pretax ≤ 0 → default
    assert xe.effective_tax_rate_multi([80.0], [100.0]) == pytest.approx(0.5)     # clamp


# --------------------------------------------------------------------------- #
# Task 6 — deuda sin doble conteo, caja con fallback, identidades contables
# --------------------------------------------------------------------------- #

def _lookup(values: dict[str, float]):
    """Simula point_value: primer concepto de la tupla presente en `values`."""
    def get(concepts):
        for c in concepts:
            if c in values:
                return {"value": values[c], "period": "FY2025", "source": c}
        return None
    return get


def test_debt_aapl_like_no_double_count_and_commercial_paper_counted():
    d = xe.resolve_debt(_lookup({"LongTermDebt": 90.0, "LongTermDebtNoncurrent": 78.0,
                                 "LongTermDebtCurrent": 12.0, "CommercialPaper": 8.0}), "FY2025")
    assert d["long_term_debt"]["value"] == 78.0
    assert d["short_term_debt"]["value"] == 20.0          # 12 current LTD + 8 CP
    assert d["flags"] == []                                 # 90 == 78 + 12


def test_debt_total_including_current_is_split_not_added():
    d = xe.resolve_debt(_lookup({"LongTermDebt": 100.0, "LongTermDebtCurrent": 10.0}), "FY2025")
    assert d["long_term_debt"] == {"value": 90.0, "period": "FY2025",
                                   "source": "derived:LongTermDebt-LongTermDebtCurrent"}
    assert d["short_term_debt"]["value"] == 10.0


def test_debt_current_total_wins_over_components():
    d = xe.resolve_debt(_lookup({"DebtCurrent": 15.0, "LongTermDebtCurrent": 10.0,
                                 "ShortTermBorrowings": 5.0}), "FY2025")
    assert d["short_term_debt"] == {"value": 15.0, "period": "FY2025", "source": "DebtCurrent"}


def test_debt_inconsistent_components_flagged():
    d = xe.resolve_debt(_lookup({"LongTermDebt": 120.0, "LongTermDebtNoncurrent": 78.0,
                                 "LongTermDebtCurrent": 12.0}), "FY2025")
    assert any("LongTermDebt" in f for f in d["flags"])


def test_debt_longtermdebt_plus_debtcurrent_without_current_ltd_not_double_counted():
    # LTD 100 (incluye 10 corriente), DebtCurrent 15 (= 10 corriente + 5 CP): total debe ser 105, no 115
    d = xe.resolve_debt(_lookup({"LongTermDebt": 100.0, "DebtCurrent": 15.0, "CommercialPaper": 5.0}), "FY2025")
    assert d["long_term_debt"]["value"] == 90.0 and d["short_term_debt"]["value"] == 15.0
    assert any("inferred from DebtCurrent" in f for f in d["flags"])


def test_finance_leases_added_only_when_debt_concept_excludes_them():
    plain = xe.resolve_debt(_lookup({"LongTermDebtNoncurrent": 50.0, "FinanceLeaseLiability": 4.0}), "FY2025")
    assert plain["finance_leases"]["value"] == 4.0
    bundled = xe.resolve_debt(_lookup({"LongTermDebtAndCapitalLeaseObligations": 50.0,
                                       "FinanceLeaseLiability": 4.0}), "FY2025")
    assert bundled["finance_leases"]["value"] == 0.0       # already inside the debt concept


def test_cash_falls_back_to_restricted_and_flags_it():
    c = xe.resolve_cash(_lookup({"CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": 9.0}),
                        "FY2025", "X")
    assert c["value"] == 9.0 and "Restricted" in c["source"]


def test_cash_missing_fails_loud():
    with pytest.raises(xe.DataUnavailableError, match="cash"):
        xe.resolve_cash(_lookup({}), "FY2025", "X")


def test_balance_identity_raises_on_assets_vs_l_and_e_mismatch():
    with pytest.raises(xe.DataUnavailableError, match="identity"):
        xe.balance_identity_flags(_lookup({"Assets": 100.0, "LiabilitiesAndStockholdersEquity": 80.0}),
                                  "FY2025", "X")


def test_balance_identity_flags_liabilities_plus_equity_gap():
    flags = xe.balance_identity_flags(_lookup({"Assets": 100.0, "LiabilitiesAndStockholdersEquity": 100.0,
                                               "Liabilities": 60.0, "StockholdersEquity": 30.0}), "FY2025", "X")
    assert flags and "Liabilities+Equity" in flags[0]


def test_build_raw_emits_resolved_debt_sti_and_flags():
    """Integración build_raw ← resolve_debt/resolve_cash: STI propio, deuda sin doble conteo."""
    inc, bs, cf = _min_frames()
    bs.loc["LongTermDebt"] = ["d", False, 90.0, 80.0]
    bs.loc["LongTermDebtNoncurrent"] = ["d", False, 78.0, 70.0]
    bs.loc["LongTermDebtCurrent"] = ["d", False, 12.0, 10.0]
    bs.loc["ShortTermInvestments"] = ["s", False, 40.0, 30.0]
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 9, 27), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    b = raw["balance_sheet"]
    assert b["long_term_debt"]["value"] == 78.0 and b["short_term_debt"]["value"] == 12.0
    assert b["cash_and_equivalents"]["value"] == 50.0
    assert b["short_term_investments"] == {"value": 40.0, "period": "FY2025", "source": "ShortTermInvestments"}
    assert raw["data_quality_flags"] == []


def test_build_raw_missing_cash_fails_loud():
    inc, bs, cf = _min_frames()
    bs = bs.drop(index="CashAndCashEquivalentsAtCarryingValue")
    with pytest.raises(xe.DataUnavailableError, match="cash"):
        xe.build_raw(ticker="x", cik="1", period_end=date(2025, 9, 27), inc_df=inc, bs_df=bs,
                     cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)


# --------------------------------------------------------------------------- #
# Task 7 — huecos de años y snapshot trimestral real
# --------------------------------------------------------------------------- #

def test_backfill_gaps_fills_interior_year_from_facts_with_provenance():
    s = [{"value": 529.8, "period": "FY2026", "source": "Revenues"},
         {"value": 467.9, "period": "FY2024", "source": "Revenues"}]
    out = xe.backfill_gaps(s, FakeFacts({("Revenues", "FY2025"): 496.0}))
    assert [i["period"] for i in out] == ["FY2026", "FY2025", "FY2024"]
    assert out[1]["source"] == "facts:Revenues"


def test_backfill_gaps_never_extrapolates_or_touches_derived_series():
    facts = FakeFacts({("Revenues", "FY2027"): 1.0, ("Revenues", "FY2023"): 1.0})
    s = [{"value": 2.0, "period": "FY2026", "source": "Revenues"},
         {"value": 1.5, "period": "FY2024", "source": "Revenues"}]
    assert [i["period"] for i in xe.backfill_gaps(s, facts)] == ["FY2026", "FY2024"]
    derived = [{"value": 2.0, "period": "FY2026", "source": "derived:GrossProfit-SGA"},
               {"value": 1.5, "period": "FY2024", "source": "derived:GrossProfit-SGA"}]
    assert xe.backfill_gaps(derived, facts) == derived


def test_latest_quarter_column_parses_xbrl_and_facts_formats():
    cols = ["concept", "label", "2025-03-29 (Q2)", "2025-06-28 (Q3)", "2025-06-28 (YTD)"]
    assert xe.latest_quarter_column(cols) == "2025-06-28 (Q3)"
    assert xe.latest_quarter_column(["Q4 2025", "Q1 2026"]) == "Q1 2026"  # bug string-sort


def test_quarter_value_reads_concept_column_with_prefix():
    df = pd.DataFrame({"concept": ["us-gaap_Revenues", "us-gaap_OperatingIncomeLoss"],
                       "label": ["Revenue", "OpInc"], "2025-06-28 (Q3)": [94.0, 28.0]})
    assert xe.quarter_value(df, ("Revenues",), "2025-06-28 (Q3)") == 94.0


def test_build_raw_backfills_interior_gap_in_annual_series():
    """BBW-like: la vista curada omite FY2024; build_raw lo rellena desde raw facts (EC-88)."""
    cols = ("FY 2025", "FY 2024", "FY 2023")
    inc, bs, cf = _min_frames(cols)
    inc.loc["Revenues", "FY 2024"] = NaN
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts({("Revenues", "FY2024"): 95.0}), shares_outstanding=1.0)
    assert [(i["period"], i["source"]) for i in raw["annual"]["revenue"]] == [
        ("FY2025", "Revenues"), ("FY2024", "facts:Revenues"), ("FY2023", "Revenues")]


# --------------------------------------------------------------------------- #
# Task 8 — extracciones nuevas para FCFF (consumidas por el Bloque C)
# --------------------------------------------------------------------------- #

def test_extract_annual_prefers_curated_then_facts_on_revenue_labels():
    inc = make_df({"Revenues": {"FY 2025": 100.0, "FY 2024": 90.0}})
    cf = make_df({"ShareBasedCompensation": {"FY 2025": 5.0, "FY 2024": 4.0}})
    facts = FakeFacts({("InterestExpense", "FY2025"): 2.0, ("InterestExpense", "FY2024"): 1.5})
    labels = ["FY2025", "FY2024"]
    assert xe.extract_annual(xe.SBC, (inc, cf), facts, labels)[0] == {
        "value": 5.0, "period": "FY2025", "source": "ShareBasedCompensation"}
    ie = xe.extract_annual(xe.INTEREST_EXPENSE, (inc, cf), facts, labels)
    assert [(i["period"], i["source"]) for i in ie] == [("FY2025", "facts:InterestExpense"),
                                                         ("FY2024", "facts:InterestExpense")]


def test_build_raw_emits_fcff_inputs_and_memo_items():
    inc, bs, cf = _min_frames()
    inc.loc["WeightedAverageNumberOfDilutedSharesOutstanding"] = ["w", False, 10.4, 10.6]
    inc.loc["WeightedAverageNumberOfSharesOutstandingBasic"] = ["w", False, 10.0, 10.2]
    bs.loc["OperatingLeaseLiability"] = ["l", False, 30.0, 28.0]
    raw = xe.build_raw(ticker="abc", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts({("InvestmentIncomeInterest", "FY2025"): 1.2}),
                       shares_outstanding=10.0)
    assert raw["annual"]["diluted_shares"][0]["value"] == 10.4
    assert raw["annual"]["basic_shares_weighted"][0]["value"] == 10.0
    assert raw["annual"]["interest_income"][0]["source"] == "facts:InvestmentIncomeInterest"
    assert raw["balance_sheet"]["operating_lease_liabilities"]["value"] == 30.0


def test_build_raw_operating_lease_sums_components_and_new_memo_items():
    """Sin OperatingLeaseLiability total: noncurrent + current; ROU, participaciones, leasing principal."""
    inc, bs, cf = _min_frames()
    bs.loc["OperatingLeaseLiabilityNoncurrent"] = ["l", False, 25.0, 20.0]
    bs.loc["OperatingLeaseLiabilityCurrent"] = ["l", False, 5.0, 4.0]
    bs.loc["OperatingLeaseRightOfUseAsset"] = ["r", False, 29.0, 23.0]
    bs.loc["EquityMethodInvestments"] = ["e", False, 7.0, 6.0]
    cf.loc["FinanceLeasePrincipalPayments"] = ["f", False, 3.0, 2.0]
    raw = xe.build_raw(ticker="abc", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    b = raw["balance_sheet"]
    assert b["operating_lease_liabilities"]["value"] == 30.0
    assert b["operating_lease_liabilities"]["source"].startswith("derived:")
    assert b["operating_rou_asset"]["value"] == 29.0
    assert b["equity_method_investments"]["value"] == 7.0
    assert b["minority_interest"] is None and b["preferred_stock"] is None
    assert raw["annual"]["finance_lease_principal_payments"][0]["value"] == 3.0


# --------------------------------------------------------------------------- #
# Task 9 — balance_history por año (base del IC por año y de suggest_wacc)
# --------------------------------------------------------------------------- #

def test_build_raw_emits_balance_history_per_fy_column():
    inc, bs, cf = _min_frames()
    bs.loc["LongTermDebtNoncurrent"] = ["d", False, 78.0, 70.0]
    bs.loc["ShortTermInvestments"] = ["s", False, 40.0, 30.0]
    bs.loc["LiabilitiesCurrent"] = ["c", False, 60.0, 55.0]
    bs.loc["OperatingLeaseRightOfUseAsset"] = ["r", False, 9.0, 8.0]
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 9, 27), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    h = raw["balance_history"]
    assert [y["period"] for y in h] == ["FY2025", "FY2024"]
    assert h[1] == {"period": "FY2024", "total_assets": 500.0, "stockholders_equity": 200.0,
                    "total_debt": 70.0, "short_term_debt": 0.0, "cash": 50.0,
                    "short_term_investments": 30.0, "current_liabilities": 55.0,
                    "operating_rou_asset": 8.0, "long_term_investments": None}



# ── H1 integración (verificación en vivo, PRDO): concepto antiguo no puede "ganar" a años recientes ──
def test_merge_annual_takes_first_concept_with_data_per_year_not_first_concept_overall():
    """PRDO real: PaymentsToAcquirePropertyPlantAndEquipment solo tiene FY2016 en la vista curada y
    el capex reciente está en PaymentsForCapitalImprovements (solo en facts). Antes ganaba el concepto
    viejo y el FCF quedaba anclado a FY2016 (EC-90)."""
    cf = make_df({"PaymentsToAcquirePropertyPlantAndEquipment": {"FY 2025": NaN, "FY 2024": NaN, "FY 2016": 4.1}})
    facts = FakeFacts({("PaymentsForCapitalImprovements", "FY2025"): 8.6,
                       ("PaymentsForCapitalImprovements", "FY2024"): 4.6})
    out = xe.merge_annual(xe.CAPEX_CONCEPTS, (cf,), facts, ["FY2025", "FY2024", "FY2016"])
    assert out == [
        {"value": 8.6, "period": "FY2025", "source": "facts:PaymentsForCapitalImprovements"},
        {"value": 4.6, "period": "FY2024", "source": "facts:PaymentsForCapitalImprovements"},
        {"value": 4.1, "period": "FY2016", "source": "PaymentsToAcquirePropertyPlantAndEquipment"},
    ]


def test_merge_annual_prefers_curated_then_chain_order_then_facts():
    inc = make_df({"Revenues": {"FY 2025": 100.0, "FY 2024": NaN},
                   "RevenueFromContractWithCustomerExcludingAssessedTax": {"FY 2025": 99.0, "FY 2024": 90.0}})
    out = xe.merge_annual(("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"), (inc,),
                          FakeFacts(), ["FY2025", "FY2024"])
    assert [(i["period"], i["value"], i["source"]) for i in out] == [
        ("FY2025", 99.0, "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("FY2024", 90.0, "RevenueFromContractWithCustomerExcludingAssessedTax")]


def test_build_raw_capex_uses_recent_concept_from_facts():
    inc, bs, cf = _min_frames()
    cf.loc["PaymentsToAcquirePropertyPlantAndEquipment"] = ["capex", False] + [float("nan")] * (len(cf.columns) - 2)
    facts = FakeFacts({("PaymentsForCapitalImprovements", "FY2025"): 3.0,
                       ("PaymentsForCapitalImprovements", "FY2024"): 2.0})
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 9, 27), inc_df=inc, bs_df=bs, cf_df=cf,
                       facts=facts, shares_outstanding=10.0)
    assert raw["annual"]["capex"][0] == {"value": 3.0, "period": "FY2025", "source": "facts:PaymentsForCapitalImprovements"}



def test_build_raw_cash_flow_series_include_backfilled_revenue_years():
    """BBW real: FY2025 falta en la vista curada pero existe en facts. El revenue se rellenaba
    (backfill) pero la rejilla de etiquetas de OCF/capex se tomaba ANTES del relleno, así que
    OCF/capex/FCF seguían sin FY2025 (EC-88 incompleto)."""
    inc, bs, cf = _min_frames(("FY 2026", "FY 2024", "FY 2023"))
    facts = FakeFacts({("Revenues", "FY2025"): 95.0,
                       ("NetCashProvidedByUsedInOperatingActivities", "FY2025"): 28.0})
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2026, 1, 31), inc_df=inc, bs_df=bs, cf_df=cf,
                       facts=facts, shares_outstanding=10.0)
    assert [i["period"] for i in raw["annual"]["revenue"]] == ["FY2026", "FY2025", "FY2024", "FY2023"]
    ocf = {i["period"]: i for i in raw["annual"]["operating_cash_flow"]}
    assert ocf["FY2025"] == {"value": 28.0, "period": "FY2025",
                             "source": "facts:NetCashProvidedByUsedInOperatingActivities"}



def test_balance_history_covers_backfilled_years_from_facts():
    """BBW real: balance_history no tenía FY2025 (solo recorría columnas curadas). Debe usar la
    misma rejilla de años que las series de resultados, con fallback a facts (EC-88)."""
    inc, bs, cf = _min_frames(("FY 2026", "FY 2024"))
    facts = FakeFacts({("Revenues", "FY2025"): 95.0, ("Assets", "FY2025"): 480.0,
                       ("StockholdersEquity", "FY2025"): 190.0,
                       ("CashAndCashEquivalentsAtCarryingValue", "FY2025"): 40.0})
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2026, 1, 31), inc_df=inc, bs_df=bs, cf_df=cf,
                       facts=facts, shares_outstanding=10.0)
    hist = {h["period"]: h for h in raw["balance_history"]}
    assert list(hist) == ["FY2026", "FY2025", "FY2024"]
    assert hist["FY2025"]["total_assets"] == 480.0 and hist["FY2025"]["cash"] == 40.0


# --------------------------------------------------------------------------- #
# F0 — acciones desde el último 10-K, multiclase incluidas (EC-101)
# --------------------------------------------------------------------------- #

A, B, C = "us-gaap:CommonClassAMember", "us-gaap:CommonClassBMember", "us-gaap:CommonClassCMember"


def test_filing_shares_single_class_uses_cover():
    out = xe.resolve_filing_shares(cover=[(15.0e9, None)], diluted=[(15.2e9, None)])
    assert out == {"shares_outstanding": 15.0e9, "as_converted_diluted": None, "flag": None}


def test_filing_shares_visa_like_uses_as_converted_class_a_diluted():
    """V real (10-K FY2025): portada por clase (A 1.688 M, B1 4,8 M, B2 120 M, C 8,9 M) y diluidas
    solo por clase; la de la clase A (1.966 M) es la 'as-converted' (incluye B y C convertidas)."""
    out = xe.resolve_filing_shares(
        cover=[(1_687.6e6, A), (4.8e6, "v:CommonClassB1Member"), (120.3e6, "v:CommonClassB2Member"),
               (8.9e6, C)],
        diluted=[(1_966e6, A), (5e6, "v:CommonClassB1Member"), (120e6, "v:CommonClassB2Member"),
                 (9e6, C)])
    assert out["shares_outstanding"] is None
    assert out["as_converted_diluted"] == 1_966e6
    assert "4 classes" in out["flag"] and "as-converted" in out["flag"]


def test_filing_shares_googl_like_prefers_total_diluted_over_per_class():
    """GOOGL: diluidas totales sin dimensión + por clase (cada una solo su clase, < suma)."""
    out = xe.resolve_filing_shares(
        cover=[(5_822e6, A), (837e6, B), (5_438e6, "goog:CapitalClassCMember")],
        diluted=[(12_230e6, None), (6_671e6, A), (849e6, B), (5_559e6, "goog:CapitalClassCMember")])
    assert out["as_converted_diluted"] == 12_230e6 and out["shares_outstanding"] is None


def test_filing_shares_multiclass_without_as_converted_sums_cover_with_flag():
    out = xe.resolve_filing_shares(cover=[(80e6, A), (20e6, B)], diluted=[(50e6, A)])
    assert out["shares_outstanding"] == 100e6 and out["as_converted_diluted"] is None
    assert "conversion ratios not applied" in out["flag"]


def test_filing_shares_nothing_reported():
    assert xe.resolve_filing_shares(cover=[], diluted=[]) == {
        "shares_outstanding": None, "as_converted_diluted": None, "flag": None}


def _xbrl_facts_df(rows):
    """Forma de XBRL.facts.to_dataframe() (edgartools 5.59.1, verificada en vivo con V y MA)."""
    cols = ["concept", "value", "period_start", "period_end", "period_instant",
            "dim_us-gaap_StatementClassOfStockAxis", "dim_dei_LegalEntityAxis"]
    return pd.DataFrame([dict(zip(cols, r)) for r in rows])


def test_share_facts_from_xbrl_df_filters_current_year_and_class_axis_only():
    cls = "dim_us-gaap_StatementClassOfStockAxis"
    df = _xbrl_facts_df([
        ("dei:EntityCommonStockSharesOutstanding", "885216386", NaN, NaN, "2026-02-06", A, NaN),
        ("dei:EntityCommonStockSharesOutstanding", "6595925", NaN, NaN, "2026-02-06", B, NaN),
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "906000000",
         "2025-01-01", "2025-12-31", NaN, NaN, NaN),
        # año anterior: fuera
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "927000000",
         "2024-01-01", "2024-12-31", NaN, NaN, NaN),
        # trimestre del año corriente: fuera (no es anual)
        ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "900000000",
         "2025-10-01", "2025-12-31", NaN, NaN, NaN),
        # otra dimensión (co-registrante): fuera
        ("dei:EntityCommonStockSharesOutstanding", "1", NaN, NaN, "2026-02-06", NaN, "x:SubMember"),
    ])
    cover, diluted = xe.share_facts_from_xbrl_df(df, date(2025, 12, 31))
    assert sorted(cover) == sorted([(885216386.0, A), (6595925.0, B)])
    assert diluted == [(906000000.0, None)]
    assert cls in df.columns


def test_build_raw_injects_as_converted_diluted_when_series_lacks_current_year():
    """V: companyfacts no trae diluidas (solo por clase) → la del 10-K entra en la serie."""
    inc, bs, cf = _min_frames()
    raw = xe.build_raw(ticker="v", cik="1", period_end=date(2025, 9, 30), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=None,
                       as_converted_diluted=1_966e6, share_flags=["multi-class flag"])
    assert raw["annual"]["diluted_shares"][0] == {
        "value": 1_966e6, "period": "FY2025",
        "source": "10-K:WeightedAverageNumberOfDilutedSharesOutstanding (as-converted)"}
    assert "multi-class flag" in raw["data_quality_flags"]
    assert raw["shares"]["shares_outstanding"]["value"] is None


def test_build_raw_keeps_companyfacts_diluted_when_current_year_present():
    inc, bs, cf = _min_frames()
    inc.loc["WeightedAverageNumberOfDilutedSharesOutstanding"] = ["d", False, 906e6, 927e6]
    raw = xe.build_raw(ticker="ma", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=None,
                       as_converted_diluted=906e6)
    assert raw["annual"]["diluted_shares"][0]["source"] == "WeightedAverageNumberOfDilutedSharesOutstanding"


# --------------------------------------------------------------------------- #
# F0 — caja presentada junto con inversiones a corto en una sola línea (EC-102)
# --------------------------------------------------------------------------- #

COMBINED = "CashCashEquivalentsAndShortTermInvestments"


def _combined_frames():
    """TGT-like: el balance presenta 'Cash and cash equivalents' con el concepto combinado."""
    inc, bs, cf = _min_frames()
    bs = bs.drop(index="CashAndCashEquivalentsAtCarryingValue")
    bs.loc[COMBINED] = ["c", False, 5_488.0, 4_762.0]
    return inc, bs, cf


def test_build_raw_uses_combined_cash_line_before_narrow_cash_fact():
    """TGT real: sin el concepto combinado en la cadena, caía a facts:Cash (0,25 B frente a 5,49 B)."""
    inc, bs, cf = _combined_frames()
    facts = FakeFacts({("Cash", "FY2025"): 250.0, ("Cash", "FY2024"): 276.0})
    raw = xe.build_raw(ticker="tgt", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=facts, shares_outstanding=10.0)
    assert raw["balance_sheet"]["cash_and_equivalents"] == {
        "value": 5_488.0, "period": "FY2025", "source": COMBINED}
    assert [(h["period"], h["cash"]) for h in raw["balance_history"]] == [
        ("FY2025", 5_488.0), ("FY2024", 4_762.0)]


def test_combined_cash_line_marks_sti_as_included_to_avoid_double_count():
    """Si la caja ya incluye las inversiones a corto, un STI de las notas no se suma otra vez (EC-81)."""
    inc, bs, cf = _combined_frames()
    facts = FakeFacts({("ShortTermInvestments", "FY2025"): 300.0,
                       ("ShortTermInvestments", "FY2024"): 200.0})
    raw = xe.build_raw(ticker="tgt", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=facts, shares_outstanding=10.0)
    assert raw["balance_sheet"]["short_term_investments"] == {
        "value": 0.0, "period": "FY2025", "source": f"included in {COMBINED}"}
    assert [h["short_term_investments"] for h in raw["balance_history"]] == [0.0, 0.0]


def test_pure_cash_line_wins_over_combined_subtotal_and_keeps_sti():
    inc, bs, cf = _min_frames()
    bs.loc[COMBINED] = ["c", False, 90.0, 80.0]
    bs.loc["ShortTermInvestments"] = ["s", False, 40.0, 30.0]
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    assert raw["balance_sheet"]["cash_and_equivalents"]["source"] == "CashAndCashEquivalentsAtCarryingValue"
    assert raw["balance_sheet"]["short_term_investments"]["value"] == 40.0


# --------------------------------------------------------------------------- #
# F0 — beneficio neto cuando la empresa solo publica ProfitLoss (EC-103)
# --------------------------------------------------------------------------- #

NCI = "NetIncomeLossAttributableToNoncontrollingInterest"


def _profitloss_frames():
    """MA/CAT-like: sin NetIncomeLoss; ProfitLoss (incluye minoritarios) + parte de minoritarios."""
    inc, bs, cf = _min_frames()
    inc = inc.drop(index="NetIncomeLoss")
    inc.loc["ProfitLoss"] = ["p", False, 14_968.0, 12_874.0]
    inc.loc[NCI] = ["n", False, 21.0, 16.0]
    return inc, bs, cf


def test_net_income_derived_from_profit_loss_minus_noncontrolling_interest():
    inc, bs, cf = _profitloss_frames()
    raw = xe.build_raw(ticker="ma", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    src = f"derived:ProfitLoss-{NCI}"
    assert raw["annual"]["net_income"] == [
        {"value": 14_947.0, "period": "FY2025", "source": src},
        {"value": 12_858.0, "period": "FY2024", "source": src}]


def test_net_income_profit_loss_without_nci_is_used_and_labelled():
    inc, bs, cf = _profitloss_frames()
    inc = inc.drop(index=NCI)
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    assert raw["annual"]["net_income"][0] == {
        "value": 14_968.0, "period": "FY2025", "source": "ProfitLoss (no noncontrolling interest reported)"}


def test_net_income_resolved_year_by_year():
    """FY2025 con NetIncomeLoss, FY2024 solo con ProfitLoss (raw facts): cada año su fuente."""
    inc, bs, cf = _min_frames()
    inc.loc["NetIncomeLoss", "FY 2024"] = NaN
    facts = FakeFacts({("ProfitLoss", "FY2024"): 13.0, (NCI, "FY2024"): 1.0})
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=facts, shares_outstanding=10.0)
    assert [(i["period"], i["value"], i["source"]) for i in raw["annual"]["net_income"]] == [
        ("FY2025", 15.0, "NetIncomeLoss"), ("FY2024", 12.0, f"derived:ProfitLoss-{NCI}")]


def test_net_income_keeps_years_outside_revenue_grid():
    """PFE real: el revenue cubre 8 años y el beneficio neto 10; no se recorta a la rejilla del revenue."""
    cols = ("FY 2025", "FY 2024", "FY 2023")
    inc, bs, cf = _min_frames(cols)
    inc.loc["Revenues", "FY 2023"] = NaN
    raw = xe.build_raw(ticker="pfe", cik="1", period_end=date(2025, 12, 31), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    assert [i["period"] for i in raw["annual"]["revenue"]] == ["FY2025", "FY2024"]
    assert [i["period"] for i in raw["annual"]["net_income"]] == ["FY2025", "FY2024", "FY2023"]
