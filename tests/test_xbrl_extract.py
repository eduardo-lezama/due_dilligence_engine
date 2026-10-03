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
