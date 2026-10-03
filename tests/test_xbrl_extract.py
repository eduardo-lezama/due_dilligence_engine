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
