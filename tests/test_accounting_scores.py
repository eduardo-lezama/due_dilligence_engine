"""
Tests de los scores contables (bloque A, A2, ADR-043). Valores esperados calculados a mano con las
fórmulas publicadas (Piotroski 2000, Altman Z'' 1995, Beneish 1999, Sloan 1996).
"""
import pytest

from due_diligence.tools.accounting_scores import (
    altman_z2,
    beneish_m,
    compute_accounting_scores,
    piotroski_f,
    sloan_accruals,
)

# Tres años, más reciente primero.
_T = {"period": "FY2025", "total_assets": 1100.0, "net_income": 80.0, "ocf": 100.0, "total_debt": 300.0,
      "current_assets": 450.0, "current_liabilities": 200.0, "shares": 98.0, "gross_profit": 440.0,
      "revenue": 1100.0, "retained_earnings": 300.0, "ebit": 150.0, "equity": 500.0}
_P = {"period": "FY2024", "total_assets": 1000.0, "net_income": 60.0, "ocf": 80.0, "total_debt": 300.0,
      "current_assets": 400.0, "current_liabilities": 200.0, "shares": 100.0, "gross_profit": 380.0,
      "revenue": 1000.0}
_PP = {"period": "FY2023", "total_assets": 900.0, "net_income": 50.0}


def test_piotroski_eight_of_nine_hand_computed():
    """Todas las señales salen salvo la rotación: 1100/1000 = 1,10 < 1000/900 = 1,11."""
    r = piotroski_f([_T, _P, _PP])
    assert r.value == 8.0 and r.reading == "8/9 — strong (8–9)"
    assert r.components["Asset turnover up"] == 0.0 and r.components["ΔROA > 0"] == 1.0
    assert r.period == "FY2025 vs FY2024"


def test_piotroski_partial_when_a_signal_is_missing_is_not_classified():
    t = {**_T, "shares": None}
    r = piotroski_f([t, _P, _PP])
    assert r.value is None and r.reading.startswith("7 of 8 available signals (missing: No share issuance)")


def test_altman_z2_hand_computed_safe_zone():
    # X1 = 250/1100, X2 = 300/1100, X3 = 150/1100, X4 = 500/600 → 1,4909 + 0,8891 + 0,9164 + 0,875 = 4,17
    r = altman_z2([_T])
    assert r.value == pytest.approx(4.17) and "safe zone" in r.reading


def test_altman_z2_not_meaningful_with_negative_equity():
    """AZO/MCD: patrimonio negativo por recompras → n/m, no una falsa zona de estrés."""
    r = altman_z2([{**_T, "equity": -500.0}])
    assert r.value is None and r.reading.startswith("n/m")


def test_altman_z2_missing_input_is_na_with_list():
    r = altman_z2([{**_T, "retained_earnings": None}])
    assert r.value is None and r.reading == "n/a — missing: retained_earnings"


def _beneish_rows():
    """Año t = año anterior × 1,1 en todo → todos los índices = 1 salvo SGI = 1,1; TATA = −20/1100."""
    p = {"period": "FY2024", "receivables": 100.0, "revenue": 1000.0, "gross_profit": 400.0,
         "current_assets": 400.0, "ppe_net": 300.0, "total_assets": 1000.0, "da": 50.0, "sga": 150.0,
         "current_liabilities": 200.0, "total_debt": 300.0}
    t = {k: (v * 1.1 if isinstance(v, float) else v) for k, v in p.items()}
    t.update(period="FY2025", net_income=80.0, ocf=100.0)
    return [t, p]


def test_beneish_hand_computed_no_flag():
    # −4,84 + 0,920 + 0,528 + 0,404 + 0,892·1,1 + 0,115 − 0,172 + 4,679·(−0,018182) − 0,327 = −2,48
    r = beneish_m(_beneish_rows())
    assert r.value == pytest.approx(-2.48) and "no manipulation flag" in r.reading
    assert r.components["SGI"] == pytest.approx(1.1) and r.components["DSRI"] == pytest.approx(1.0)


def test_beneish_flags_receivables_growing_much_faster_than_sales():
    t, p = _beneish_rows()
    t = {**t, "receivables": 330.0}          # DSRI = 3 → +1,84 en M
    r = beneish_m([t, p])
    assert r.value > -1.78 and "possible earnings manipulation flag" in r.reading


def test_beneish_missing_sga_is_na_not_neutral():
    t, p = _beneish_rows()
    r = beneish_m([{**t, "sga": None}, p])
    assert r.value is None and "sga FY2025" in r.reading


def test_sloan_accruals_hand_computed():
    r = sloan_accruals([_T, _P])     # (80 − 100) / ((1100 + 1000)/2) = −1,905 %
    assert r.value == pytest.approx(-0.019, abs=1e-4)
    assert r.reading == "-1.9% of assets — earnings below operating cash flow"


def test_single_year_gives_na_for_comparative_scores():
    s = compute_accounting_scores([_T])
    assert s.piotroski.value is None and "previous fiscal year" in s.piotroski.reading
    assert s.beneish_m.value is None and s.sloan_accruals.value is None
    assert s.altman_z2.value == pytest.approx(4.17)      # Z'' solo necesita el último balance


def test_rows_from_raw_merges_series_and_balance_and_stops_at_gap():
    from due_diligence.tools.accounting_scores import rows_from_raw
    mv = lambda v, p: {"value": v, "period": p, "source": "x"}  # noqa: E731
    raw = {"annual": {"revenue": [mv(110.0, "FY2025"), mv(100.0, "FY2024"), mv(80.0, "FY2022")],
                      "depreciation_amortization": [mv(-5.0, "FY2025")]},
           "balance_history": [{"period": "FY2025", "total_assets": 500.0, "stockholders_equity": 200.0},
                               {"period": "FY2024", "total_assets": 450.0, "receivables": None}]}
    rows = rows_from_raw(raw)
    assert [r["period"] for r in rows] == ["FY2025", "FY2024"]        # FY2023 falta → corta
    assert rows[0] == {"period": "FY2025", "revenue": 110.0, "da": 5.0, "total_assets": 500.0, "equity": 200.0}
    assert "receivables" not in rows[1]                               # None no se convierte en 0
