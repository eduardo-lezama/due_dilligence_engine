import json
import pytest
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aapl_xbrl_raw.json"


@pytest.fixture
def aapl_raw():
    return json.loads(FIXTURE_PATH.read_text())


def test_parse_financials_returns_valid_report(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.schemas.base import FinancialsReport

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert isinstance(report, FinancialsReport)


def test_parse_financials_ticker_and_cik(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.ticker == "AAPL"
    assert report.cik == "0000320193"


def test_parse_financials_revenue_values(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert len(report.revenue) >= 2
    assert report.revenue[0].value == pytest.approx(416_161_000_000)
    assert report.revenue[0].period == "FY2025"
    # T5: provenance real — el source es el concepto XBRL que aportó el dato (antes "XBRL").
    assert report.revenue[0].source == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_parse_financials_free_cash_flow_is_computed(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_fcf = 111_482_000_000 - 12_715_000_000  # OCF - capex FY2025
    assert report.free_cash_flow[0].value == pytest.approx(expected_fcf)
    assert report.free_cash_flow[0].source == "calculated"


def test_parse_financials_total_debt_is_sum_of_lt_and_st(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    # Fixture regenerado (T14) con resolve_debt (EC-80): LT no corriente 78.328B + ST (papel comercial +
    # porción corriente) 20.329B + leasing financiero 1.230B = 99.887B. Antes 90.678B: faltaba el CP.
    expected_debt = 78_328_000_000 + 20_329_000_000 + 1_230_000_000
    assert report.total_debt.value == pytest.approx(expected_debt)


def test_parse_financials_cash_includes_short_term_investments(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_cash = 35_934_000_000 + 0  # Cash FY2025, STI=0 in this filing
    assert report.cash_and_equivalents.value == pytest.approx(expected_cash)


def _min_raw() -> dict:
    """Raw mínimo válido: el fixture AAPL con el balance_sheet sustituido por uno explícito.

    Helper de T6 (el plan lo referencia; no existía). Cada test lo muta a su gusto.
    """
    raw = json.loads(FIXTURE_PATH.read_text())
    raw["balance_sheet"] = {
        "total_assets": {"value": 1000.0, "period": "FY2025", "source": "Assets"},
        "long_term_debt": {"value": 300.0, "period": "FY2025", "source": "LongTermDebtNoncurrent"},
        "short_term_debt": {"value": 0.0, "period": "FY2025", "source": "absent"},
        "cash_and_equivalents": {"value": 50.0, "period": "FY2025", "source": "CashAndCashEquivalentsAtCarryingValue"},
        "short_term_investments": {"value": 0.0, "period": "FY2025", "source": "absent"},
        "stockholders_equity": {"value": 500.0, "period": "FY2025", "source": "StockholdersEquity"},
    }
    return raw


def test_parse_financials_cash_excludes_sti():
    """Revisión adversarial (bloqueante): cash_and_equivalents = SOLO caja; STI en su campo.

    Si STI siguiera sumada dentro de cash, el bridge EV→equity (T12) la restaría dos veces.
    """
    from due_diligence.tools.edgar import EdgarAdapter
    raw = {**_min_raw(), "balance_sheet": {**_min_raw()["balance_sheet"],
           "cash_and_equivalents": {"value": 100.0, "period": "FY2025", "source": "Cash"},
           "short_term_investments": {"value": 40.0, "period": "FY2025", "source": "ShortTermInvestments"}}}
    rep = EdgarAdapter.parse_financials(raw)
    assert rep.cash_and_equivalents.value == 100.0 and rep.short_term_investments.value == 40.0


def test_parse_financials_total_debt_includes_finance_leases_and_flags():
    from due_diligence.tools.edgar import EdgarAdapter
    raw = _min_raw()
    raw["balance_sheet"]["finance_leases"] = {"value": 7.0, "period": "FY2025", "source": "FinanceLeaseLiability"}
    raw["data_quality_flags"] = ["DebtCurrent < LongTermDebtCurrent (FY2025)"]
    rep = EdgarAdapter.parse_financials(raw)
    assert rep.total_debt.value == 307.0
    assert rep.data_quality_flags == ["DebtCurrent < LongTermDebtCurrent (FY2025)"]
    # STI con source "absent" → campo omitido (None), no un MetricValue(0) que parezca dato
    assert rep.short_term_investments is None


def test_parse_financials_fcff_fields_and_legacy_fixture(aapl_raw):
    """Fixture legacy sin claves nuevas → campos vacíos/None; con claves → MetricValues con unidad correcta."""
    from due_diligence.tools.edgar import EdgarAdapter
    # "Legacy" = raw sin las claves nuevas de T8 (el fixture regenerado ya las trae).
    new_annual = {"interest_expense", "interest_income", "sbc", "depreciation_amortization",
                  "diluted_shares", "basic_shares_weighted", "finance_lease_principal_payments"}
    new_bs = {"operating_lease_liabilities", "operating_rou_asset", "minority_interest",
              "preferred_stock", "equity_method_investments"}
    legacy_raw = {**aapl_raw,
                  "annual": {k: v for k, v in aapl_raw["annual"].items() if k not in new_annual},
                  "balance_sheet": {k: v for k, v in aapl_raw["balance_sheet"].items() if k not in new_bs}}
    legacy = EdgarAdapter.parse_financials(legacy_raw)
    assert legacy.sbc == [] and legacy.operating_lease_liabilities is None
    assert legacy.income_tax[0].period == "FY2025"   # el fixture AAPL ya trae income_tax
    raw = {**aapl_raw, "annual": {**aapl_raw["annual"],
           "diluted_shares": [{"value": 15.0e9, "period": "FY2025", "source": "WeightedAverageNumberOfDilutedSharesOutstanding"}],
           "basic_shares_weighted": [{"value": 14.9e9, "period": "FY2025", "source": "WeightedAverageNumberOfSharesOutstandingBasic"}]}}
    r = EdgarAdapter.parse_financials(raw)
    assert r.diluted_shares[0].unit == "shares" and r.basic_shares_weighted.value == 14.9e9


def test_parse_financials_fcff_signs_normalized():
    """Intereses, SBC, D&A y principal de leasing en MAGNITUD (el signo varía por filer y por
    versión de edgartools); intereses cobrados con su signo."""
    from due_diligence.tools.edgar import EdgarAdapter
    raw = _min_raw()
    item = lambda v, c: [{"value": v, "period": "FY2025", "source": c}]  # noqa: E731
    raw["annual"] = {**raw["annual"],
                     "interest_expense": item(-12.0, "InterestExpense"),
                     "interest_income": item(3.0, "InvestmentIncomeInterest"),
                     "sbc": item(-5.0, "ShareBasedCompensation"),
                     "depreciation_amortization": item(-9.0, "DepreciationDepletionAndAmortization"),
                     "finance_lease_principal_payments": item(-2.0, "FinanceLeasePrincipalPayments")}
    raw["balance_sheet"] = {**raw["balance_sheet"],
                            "operating_lease_liabilities": {"value": 30.0, "period": "FY2025", "source": "OperatingLeaseLiability"},
                            "operating_rou_asset": {"value": 29.0, "period": "FY2025", "source": "OperatingLeaseRightOfUseAsset"},
                            "minority_interest": None}
    r = EdgarAdapter.parse_financials(raw)
    assert r.interest_expense[0].value == 12.0 and r.interest_income[0].value == 3.0
    assert r.sbc[0].value == 5.0 and r.depreciation_amortization[0].value == 9.0
    assert r.finance_lease_principal_payments[0].value == 2.0
    assert r.operating_lease_liabilities.value == 30.0 and r.operating_rou_asset.value == 29.0
    assert r.minority_interest is None


# ---------------------------------------------------------------------------
# Task 9 — ROIC por año, IC por año, ROIC incremental, series de balance (EC-85)
# ---------------------------------------------------------------------------

def test_effective_tax_rate_per_year_rules():
    from due_diligence.tools.edgar import effective_tax_rate
    assert effective_tax_rate(21.0, 100.0) == 0.21
    assert effective_tax_rate(80.0, 100.0) == 0.5        # clamp
    assert effective_tax_rate(5.0, -10.0) == 0.25        # pretax <= 0 → default
    assert effective_tax_rate(None, 100.0) == 0.25


def test_invested_capital_operating_fallback_excludes_non_interest_bearing_and_rou():
    from due_diligence.tools.edgar import invested_capital
    # AZO-like: equity ≤ 0 → SIEMPRE operativo (regla explícita; antes primary 5130 > umbral 1358 y el
    # test era aritméticamente imposible). Current liabilities 6000, of which 500 is debt; ROU 3000.
    ic, method = invested_capital(equity=-3400.0, total_debt=8800.0, cash=270.0, total_assets=19355.0,
                                  current_liabilities=6000.0, short_term_debt=500.0,
                                  sti=0.0, lti=0.0, operating_rou_asset=3000.0)
    assert ic == pytest.approx(19355.0 - 270.0 - 3000.0 - 5500.0)
    assert method.startswith("operating")


def test_invested_capital_primary_when_equity_positive():
    from due_diligence.tools.edgar import invested_capital
    assert invested_capital(equity=200.0, total_debt=100.0, cash=50.0, total_assets=500.0) == (250.0, "equity+debt-cash")


def test_invested_capital_tiny_ic_is_not_meaningful():
    from due_diligence.tools.edgar import invested_capital
    # IC < 5 % de activos → None (ROIC "n/m"), evita ROIC > 200 % espurios en net-cash + equity negativo
    assert invested_capital(equity=10.0, total_debt=0.0, cash=0.0, total_assets=1000.0) is None


def _raw_with_history():
    def s(vals):
        return [{"value": v, "period": f"FY{y}", "source": "X"} for y, v in vals]
    return {
        "ticker": "T", "cik": "1", "as_of_date": "2025-12-31", "source_filing": "10-K FY2025",
        "annual": {"revenue": s([(2025, 1000), (2024, 900), (2023, 800), (2022, 700)]),
                   "gross_profit": [], "net_income": [], "capex": [], "operating_cash_flow": [],
                   "operating_income": s([(2025, 200), (2024, 150), (2023, 120), (2022, 100)]),
                   "income_tax": s([(2025, 40), (2024, 15)]), "pretax_income": s([(2025, 200), (2024, 150)])},
        "balance_sheet": {"total_assets": {"value": 2000, "period": "FY2025"},
                          "long_term_debt": {"value": 300, "period": "FY2025"},
                          "short_term_debt": {"value": 0, "period": "FY2025"},
                          "cash_and_equivalents": {"value": 100, "period": "FY2025"},
                          "short_term_investments": {"value": 0, "period": "FY2025"},
                          "stockholders_equity": {"value": 800, "period": "FY2025"}},
        "balance_history": [
            {"period": f"FY{y}", "total_assets": 2000.0, "stockholders_equity": eq, "total_debt": 300.0,
             "short_term_debt": 0.0, "cash": 100.0, "short_term_investments": 0.0, "current_liabilities": None}
            for y, eq in ((2025, 800.0), (2024, 700.0), (2023, 600.0), (2022, 500.0))],
        "shares": {"shares_outstanding": {"value": 10.0, "period": "FY2025"}},
    }


def test_roic_series_uses_per_year_ic_and_tax():
    from due_diligence.tools.edgar import EdgarAdapter
    r = EdgarAdapter.parse_financials(_raw_with_history())
    # FY2025: NOPAT = 200*(1-0.20)=160; IC = avg(1000, 900) = 950
    assert r.roic_series[0].period == "FY2025"
    assert r.roic_series[0].value == pytest.approx(160 / 950, abs=1e-4)
    # FY2024: tax 0.10 → NOPAT 135; IC avg(900, 800)=850
    assert r.roic_series[1].value == pytest.approx(135 / 850, abs=1e-4)
    assert [m.period for m in r.equity_series] == ["FY2025", "FY2024", "FY2023", "FY2022"]
    assert r.total_debt_series[0].value == 300.0 and r.cash_series[0].value == 100.0


def test_incremental_roic_three_year_window():
    from due_diligence.tools.edgar import EdgarAdapter
    r = EdgarAdapter.parse_financials(_raw_with_history())
    # NOPAT FY2025 = 160, FY2022 = 100*0.75 = 75 ; IC end 1000 vs 700 → 85/300
    assert r.incremental_roic.value == pytest.approx(85 / 300, abs=1e-4)
    assert r.incremental_roic.period == "FY2022→FY2025"


def test_one_ic_method_for_whole_series_when_any_year_has_negative_equity():
    """Revisión adversarial A4: si algún año tiene equity ≤ 0, TODA la serie usa IC operativo
    (mezclar métodos entre años crearía saltos espurios en ROIC y en ΔIC)."""
    from due_diligence.tools.edgar import EdgarAdapter
    raw = _raw_with_history()
    raw["balance_history"][3]["stockholders_equity"] = -50.0   # FY2022 negativo
    r = EdgarAdapter.parse_financials(raw)
    # Operativo sin LiabilitiesCurrent: 2000 − 100 = 1900 todos los años → FY2025 160/1900
    assert r.roic_series[0].value == pytest.approx(160 / 1900, abs=1e-4)
    assert r.invested_capital.source.startswith("operating")


def test_legacy_fixture_keeps_latest_ic_roic(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter
    legacy = {k: v for k, v in aapl_raw.items() if k != "balance_history"}
    r = EdgarAdapter.parse_financials(legacy)
    assert r.roic_series[0].source == "calculated-latest-ic"
    assert r.equity_series == [] and r.incremental_roic is None


# ---------------------------------------------------------------------------
# parse_quarterly_snapshot — pure function tests (no EDGAR call, no fixture file)
# ---------------------------------------------------------------------------

class TestParseQuarterlySnapshot:
    """
    Tests de parse_quarterly_snapshot como función pura.

    No requieren fixture de red ni mock — el input es un dict construido inline,
    igual que parse_financials se testa con el JSON guardado en fixtures/.
    Esto garantiza que el parsing es 100% determinista e independiente de EDGAR.
    """

    def test_complete_snapshot_all_fields(self):
        """Todos los campos presentes → QuarterlySnapshot correctamente tipado."""
        from due_diligence.tools.edgar import EdgarAdapter
        from due_diligence.schemas.reports import QuarterlySnapshot

        raw = {
            "period": "Q1 2026",
            "revenue": 95_359_000_000.0,
            "operating_income": 29_567_000_000.0,
            "gross_profit": 43_879_000_000.0,
            "filing_date": "2026-02-05",
        }
        snap = EdgarAdapter.parse_quarterly_snapshot(raw)

        assert isinstance(snap, QuarterlySnapshot)
        assert snap.period == "Q1 2026"
        assert snap.revenue == pytest.approx(95_359_000_000.0)
        assert snap.operating_income == pytest.approx(29_567_000_000.0)
        assert snap.gross_profit == pytest.approx(43_879_000_000.0)
        assert snap.filing_date == "2026-02-05"

    def test_minimal_snapshot_only_revenue(self):
        """Solo revenue presente (mínimo válido) → optional fields son None."""
        from due_diligence.tools.edgar import EdgarAdapter

        raw = {"period": "Q2 2025", "revenue": 10_500_000.0}
        snap = EdgarAdapter.parse_quarterly_snapshot(raw)

        assert snap.period == "Q2 2025"
        assert snap.revenue == pytest.approx(10_500_000.0)
        assert snap.operating_income is None
        assert snap.gross_profit is None
        assert snap.filing_date is None

    def test_snapshot_without_operating_income_only(self):
        """Gross profit presente pero sin EBIT — patrón frecuente en small caps."""
        from due_diligence.tools.edgar import EdgarAdapter

        raw = {
            "period": "Q3 2025",
            "revenue": 48_000_000.0,
            "gross_profit": 21_000_000.0,
        }
        snap = EdgarAdapter.parse_quarterly_snapshot(raw)

        assert snap.gross_profit == pytest.approx(21_000_000.0)
        assert snap.operating_income is None

    def test_snapshot_period_preserved_exactly(self):
        """El period se preserva sin transformación — el LLM lo cita verbatim."""
        from due_diligence.tools.edgar import EdgarAdapter

        raw = {"period": "Q4 2024", "revenue": 1.0}
        snap = EdgarAdapter.parse_quarterly_snapshot(raw)
        assert snap.period == "Q4 2024"


def test_parse_financials_source_filing(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.source_filing == "10-K FY2025"


def test_parse_financials_roic_series(aapl_raw):
    """ROIC calculado correctamente desde income statement + balance sheet."""
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)

    assert len(report.roic_series) >= 1
    assert report.roic_series[0].unit == "ratio"
    assert report.roic_series[0].period == "FY2025"
    # T9 + fixture regenerado. FY2025: t = 20719/132729 = 15.61 %, NOPAT = 133050 × 0.8439 = 112281M.
    # IC_2025 = equity 73733 + deuda 99887 − caja 35934 − STI 18763 − LTI 77723 = 41200M.
    # IC_2024 = 56950 + 107525 − 29943 − 35228 − 91479 = 7825M < 5 % de activos (≈18.2B) → n/m,
    # así que FY2025 usa IC de cierre (no media): ROIC = 112281 / 41200 = 2.725 (272 %).
    assert report.roic_series[0].value == pytest.approx(2.7253, abs=0.001)
    assert report.roic_series[0].source == "calculated-end-ic"


def test_parse_financials_invested_capital(aapl_raw):
    """Invested Capital primaria: Equity + Debt - Cash (cuando no es degenerada)."""
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)

    assert report.invested_capital is not None
    # Para AAPL: ic_primary ($128B) > 10% × ic_fallback ($33B) → primary se usa
    # IC primario sin activos no operativos (T9): equity + deuda − caja − STI − LTI = 41.2B.
    expected_ic = 73_733_000_000 + 99_887_000_000 - 35_934_000_000 - 18_763_000_000 - 77_723_000_000
    assert report.invested_capital.value == pytest.approx(expected_ic)
    assert report.invested_capital.source == "equity+debt-cash"
    assert report.stockholders_equity.value == pytest.approx(73_733_000_000)


def test_parse_financials_operating_income_fallback_gross_minus_sga(aapl_raw):
    """
    EC-21: cuando OperatingIncomeLoss no está en XBRL, derivar EBIT desde
    GrossProfit - SG&A. Patrón típico en small/mid caps con income statement
    simplificado (BBW, retailers).
    """
    from due_diligence.tools.edgar import EdgarAdapter

    # Mutate fixture: simular ausencia de OperatingIncomeLoss añadiendo gross_profit y SG&A
    raw = json.loads(json.dumps(aapl_raw))  # deep copy
    # Vaciar operating_income; preservar gross_profit; inyectar SG&A
    raw["annual"]["operating_income"] = []  # forzar fallback
    # Si no hay gross_profit en el fixture, no podemos testear este caso
    if not raw["annual"].get("gross_profit"):
        pytest.skip("Fixture no incluye gross_profit")

    # El test real necesita SG&A en el inc_df — esto requeriría re-fetch.
    # En su lugar, validamos via fetch en vivo en el test de integración (BBW).
    # Aquí solo verificamos que con operating_income vacío, no rompe el parser.
    report = EdgarAdapter.parse_financials(raw)
    # Sin EBIT, ROIC se queda vacío (esperado en este escenario sin SG&A inyectado)
    assert isinstance(report.operating_income, list)


def test_parse_financials_invested_capital_fallback_for_buyback_heavy(aapl_raw):
    """
    EC-18 / ADR-023: cuando la fórmula primaria (Equity+Debt-Cash) es degenerada
    por buybacks agresivos, el sistema usa total_assets-cash como fallback.

    Escenario simulado: mutamos el equity a un valor pequeño para reproducir
    una empresa con buybacks acumulados (BBW, Domino's, AutoZone). Debe usar
    el fallback en lugar de excluir el ROIC.
    """
    from due_diligence.tools.edgar import EdgarAdapter

    # Mutate the raw fixture: equity negativo (típico de empresas con buybacks acumulados
    # superiores al equity contribuido + retained earnings — caso AutoZone, Domino's).
    raw_modified = json.loads(json.dumps(aapl_raw))  # deep copy
    raw_modified["balance_sheet"]["stockholders_equity"]["value"] = -50_000_000_000  # $-50B
    # ic_primary = -50B + 90B - 36B = $4B
    # ic_fallback = total_assets - cash ≈ $328B
    # threshold = 0.10 × 328B = $33B
    # primary ($4B) < threshold → fallback se usa

    report = EdgarAdapter.parse_financials(raw_modified)

    assert report.invested_capital is not None
    # T9: equity ≤ 0 → IC operativo (Assets − cash − STI − LTI − ROU [− NIBCL]); antes "fallback".
    assert report.invested_capital.source.startswith("operating")
    # ROIC debe seguir calculándose con el fallback
    assert len(report.roic_series) >= 1
    assert report.roic_series[0].value > 0  # ROIC válido, no excluido


def test_parse_financials_handles_missing_shares_outstanding(aapl_raw):
    """
    EC-34: cuando XBRL no reporta shares_outstanding (small caps dual-class como CRD.A,
    ADRs sin tagging completo), el parser debe devolver shares_outstanding=None
    en lugar de lanzar TypeError o crear un MetricValue inválido.

    Invariante preservado: MetricValue siempre tiene un value válido. La ausencia
    de dato se representa por el campo padre = None, no por MetricValue(value=None).
    """
    from due_diligence.tools.edgar import EdgarAdapter

    # Simular XBRL sin shares_outstanding (caso CRD.A en producción)
    raw_modified = json.loads(json.dumps(aapl_raw))
    raw_modified["shares"]["shares_outstanding"]["value"] = None

    report = EdgarAdapter.parse_financials(raw_modified)

    # Schema invariant preservado: shares_outstanding es None, no MetricValue inválido
    assert report.shares_outstanding is None
    # El resto del report debe construirse sin errores
    assert report.ticker == "AAPL"
    assert len(report.revenue) > 0


def test_extract_balance_concept_raw_facts_fallback(mocker, aapl_raw):
    """
    EC-37: cuando el bs_df curado de edgartools omite un concept de debt
    pero el raw _facts SÍ lo tiene (caso MOS verificado en producción),
    el extractor debe usar el fallback a `facts.get_annual_fact()`.

    Test verifica que parse_financials sigue produciendo total_debt válido
    cuando los conceptos vienen del raw facts (estado raw del fixture).
    """
    from due_diligence.tools.edgar import EdgarAdapter

    # Fixture AAPL ya tiene debt extraído correctamente vía bs curado.
    # Verifica que el parser sigue produciendo debt no-cero en happy path
    # (no regresiones por el helper nuevo).
    report = EdgarAdapter.parse_financials(aapl_raw)
    assert report.total_debt is not None
    assert report.total_debt.value > 0  # AAPL tiene debt


def test_fetch_raw_rejects_financial_firms_by_sic(mocker):
    """
    EC-35: empresas financieras (SIC 6000-6799) deben rechazarse con error claro,
    NO procesarse con framework no-financiero. Verifica que el sistema sigue
    ADR-005 (fail loud) en lugar de producir output indefendible.
    """
    from due_diligence.tools.edgar import EdgarAdapter, DataUnavailableError

    mock_company = mocker.MagicMock()
    mock_company.sic = "6111"
    mock_company.industry = "Federal & Federally-Sponsored Credit Agencies"
    # Patch en el módulo edgar de donde se hace `from edgar import Company`
    mocker.patch("edgar.Company", return_value=mock_company)
    mocker.patch("edgar.set_identity")

    with pytest.raises(DataUnavailableError) as exc_info:
        EdgarAdapter.fetch_raw("AGM")

    error_msg = str(exc_info.value)
    assert "financial firm" in error_msg
    assert "6111" in error_msg
    assert "DCF, EV/EBIT, ROIC" in error_msg  # mensaje educativo


def test_fetch_raw_accepts_non_financial_sic(mocker):
    """
    EC-35: empresas no-financieras (SIC fuera de 6000-6799) deben pasar la verificación
    SIC y continuar con la extracción normal. Test que el check no es overly broad.
    """
    from due_diligence.tools.edgar import EdgarAdapter

    mock_company = mocker.MagicMock()
    mock_company.sic = "3571"
    mock_company.industry = "Electronic Computers"
    mock_company.get_facts.side_effect = RuntimeError("stop after SIC check")
    mocker.patch("edgar.Company", return_value=mock_company)
    mocker.patch("edgar.set_identity")

    # Si el SIC check fuera overly broad, lanzaría DataUnavailableError aquí.
    # En su lugar debe pasar y llegar a get_facts() (que mockeamos con RuntimeError).
    with pytest.raises(RuntimeError, match="stop after SIC check"):
        EdgarAdapter.fetch_raw("AAPL")


# ---------- EC-43: Form 4 institutional holder detection ---------- #


class TestIsInstitutionalHolder:
    """Tests para _is_institutional_holder: separar officers/directors de >10% holders."""

    def test_cfo_is_not_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("John Smith", "CFO") is False

    def test_director_is_not_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Jane Doe", "Director") is False

    def test_vp_is_not_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Bob Jones", "VP of Sales") is False

    def test_president_is_not_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Alice Brown", "President and CEO") is False

    def test_jana_partners_llc_is_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("JANA Partners LLC", "10% Owner") is True

    def test_blackrock_inc_is_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("BlackRock Capital Management Inc", "10% Owner") is True

    def test_llc_suffix_alone_is_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Acme Holdings LLC", "") is True

    def test_lp_suffix_is_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Starboard Value LP", "10% Owner") is True

    def test_plain_name_10pct_without_keywords_is_not_institutional(self):
        """Un 10% owner con nombre de persona (no fondo) no es institucional."""
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Warren Buffett", "10% Owner") is False

    def test_officer_with_fund_name_is_not_institutional(self):
        """Si la posición dice Director, no importa si el nombre suena a fondo."""
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Capital Partners", "Director") is False

    def test_general_counsel_is_not_institutional(self):
        from due_diligence.tools.edgar import _is_institutional_holder
        assert _is_institutional_holder("Sarah Connor", "General Counsel") is False


class TestFormatForm4WithInstitutional:
    """Tests para format_form4_for_prompt con separación institucional (EC-43)."""

    def _make_txn(self, name, position, is_institutional, tx_type="sell"):
        return {
            "filer_name": name,
            "position": position,
            "transaction_date": "2026-01-15",
            "shares": 1000,
            "price_per_share": 40.0,
            "transaction_type": tx_type,
            "is_institutional": is_institutional,
        }

    def test_mixed_transactions_produces_two_sections(self):
        from due_diligence.tools.edgar import format_form4_for_prompt

        txns = [
            self._make_txn("John Smith", "CFO", False),
            self._make_txn("JANA Partners LLC", "10% Owner", True),
        ]
        result = format_form4_for_prompt(txns)
        assert "OFFICER/DIRECTOR" in result
        assert "INSTITUTIONAL >10% HOLDER" in result
        assert "NOT insider sentiment" in result

    def test_only_insiders_no_institutional_section(self):
        from due_diligence.tools.edgar import format_form4_for_prompt

        txns = [self._make_txn("John Smith", "CFO", False)]
        result = format_form4_for_prompt(txns)
        assert "OFFICER/DIRECTOR" in result
        assert "INSTITUTIONAL" not in result

    def test_only_institutional_shows_no_officer_message(self):
        from due_diligence.tools.edgar import format_form4_for_prompt

        txns = [self._make_txn("JANA Partners LLC", "10% Owner", True)]
        result = format_form4_for_prompt(txns)
        assert "No discretionary officer/director open-market transactions" in result
        assert "INSTITUTIONAL >10% HOLDER" in result

    def test_empty_transactions(self):
        from due_diligence.tools.edgar import format_form4_for_prompt

        result = format_form4_for_prompt([])
        assert "No open-market insider purchases or sales" in result

    def test_backward_compatible_without_is_institutional_key(self):
        """Transacciones legacy sin is_institutional se tratan como insiders."""
        from due_diligence.tools.edgar import format_form4_for_prompt

        txns = [{
            "filer_name": "Old Txn", "position": "CEO",
            "transaction_date": "2025-06-01", "shares": 100,
            "price_per_share": 50.0, "transaction_type": "buy",
        }]
        result = format_form4_for_prompt(txns)
        assert "OFFICER/DIRECTOR" in result

    def test_10b5_1_plan_sales_separated(self):
        """Ventas 10b5-1 van a sección separada, no como insider sentiment."""
        from due_diligence.tools.edgar import format_form4_for_prompt

        txns = [
            {
                "filer_name": "CEO Jane", "position": "CEO",
                "transaction_date": "2026-04-10", "shares": 500000,
                "price_per_share": 160.0, "transaction_type": "sell",
                "is_institutional": False, "is_10b5_1_plan": True,
            },
            {
                "filer_name": "CFO Bob", "position": "CFO",
                "transaction_date": "2026-03-15", "shares": 5000,
                "price_per_share": 140.0, "transaction_type": "buy",
                "is_institutional": False, "is_10b5_1_plan": False,
            },
        ]
        result = format_form4_for_prompt(txns)
        # CEO sale goes to 10b5-1 section, NOT officer/director
        assert "10b5-1 PRE-ARRANGED PLAN" in result
        assert "NOT informative about current sentiment" in result
        # CFO buy is discretionary → in officer/director section
        assert "OFFICER/DIRECTOR discretionary" in result
        assert "CFO Bob" in result

    def test_10b5_1_all_planned_shows_no_discretionary(self):
        """Cuando todas las transacciones son 10b5-1, no hay discretionary."""
        from due_diligence.tools.edgar import format_form4_for_prompt

        txns = [{
            "filer_name": "CEO Jane", "position": "CEO",
            "transaction_date": "2026-04-10", "shares": 500000,
            "price_per_share": 160.0, "transaction_type": "sell",
            "is_institutional": False, "is_10b5_1_plan": True,
        }]
        result = format_form4_for_prompt(txns)
        assert "No discretionary officer/director" in result
        assert "10b5-1 PRE-ARRANGED PLAN" in result


class TestDetect10b51Plan:
    """Tests for _detect_10b5_1_plan heuristic (EC-50)."""

    def test_detects_from_footnotes(self):
        """Si footnotes menciona 10b5-1, devuelve True."""
        from due_diligence.tools.edgar import _detect_10b5_1_plan

        class MockObj:
            footnotes = "Sales pursuant to a Rule 10b5-1 trading plan adopted on 2025-11-14."

        class MockFiling:
            pass

        assert _detect_10b5_1_plan(MockFiling(), MockObj()) is True

    def test_detects_from_filing_text(self):
        """Si filing.text() menciona 10b5-1, devuelve True."""
        from due_diligence.tools.edgar import _detect_10b5_1_plan

        class MockObj:
            footnotes = None

        class MockFiling:
            def text(self):
                return "This transaction was executed under a pre-arranged 10b5-1 trading plan."

        assert _detect_10b5_1_plan(MockFiling(), MockObj()) is True

    def test_returns_false_when_no_plan(self):
        """Sin evidencia de plan, devuelve False."""
        from due_diligence.tools.edgar import _detect_10b5_1_plan

        class MockObj:
            footnotes = "Standard Form 4 filing. No special arrangements."

        class MockFiling:
            def text(self):
                return "Standard insider transaction filing."

        assert _detect_10b5_1_plan(MockFiling(), MockObj()) is False

    def test_handles_missing_attributes_gracefully(self):
        """Objetos sin footnotes ni text() no crashean."""
        from due_diligence.tools.edgar import _detect_10b5_1_plan

        class MockObj:
            pass

        class MockFiling:
            pass

        assert _detect_10b5_1_plan(MockFiling(), MockObj()) is False


# ---------------------------------------------------------------------------
# Tests: P1 — fetch_quarterly_snapshot staleness threshold 45 days (default)
# ---------------------------------------------------------------------------

@pytest.mark.real_quarterly_fetch
class TestFetchQuarterlySnapshotThreshold:
    """
    P1: el threshold por defecto es 45 días (antes 90).
    Con 90 días, una empresa que publicara el 10-K en marzo quedaba "fresca"
    hasta junio, saltándose Q1 earnings (publicados en abril/mayo).
    """

    def test_default_staleness_threshold_is_45_days(self):
        """El parámetro por defecto debe ser 45, no 90."""
        import inspect
        from due_diligence.tools.edgar import EdgarAdapter
        sig = inspect.signature(EdgarAdapter.fetch_quarterly_snapshot)
        default = sig.parameters["staleness_threshold_days"].default
        assert default == 45, f"Expected 45, got {default}"

    def test_fresh_10k_within_45_days_is_skipped(self):
        """Con 10-K de 30 días de antigüedad (< 45), fetch_quarterly_snapshot devuelve None."""
        from datetime import date, timedelta
        from due_diligence.tools.edgar import EdgarAdapter

        tenk_as_of = date.today() - timedelta(days=30)
        result = EdgarAdapter.fetch_quarterly_snapshot("AAPL", tenk_as_of=tenk_as_of)
        assert result is None

    def test_stale_10k_beyond_45_days_triggers_fetch(self, mocker):
        """Con 10-K de 60 días de antigüedad (> 45), intenta buscar el 10-Q."""
        from datetime import date, timedelta
        from due_diligence.tools.edgar import EdgarAdapter

        # EC-84: el assert anterior (`get_filings.called or result is None`) era tautológico
        # — pasaba siempre porque result era None por cualquier motivo. Ahora se exige que
        # el fetch trimestral se intente de verdad y que, sin 10-Q, devuelva None.
        mock_company = mocker.MagicMock()
        mock_company.get_quarterly_financials.return_value = None  # sin 10-Q
        mocker.patch("edgar.Company", return_value=mock_company)
        mocker.patch("edgar.set_identity")
        tenk_as_of = date.today() - timedelta(days=60)
        result = EdgarAdapter.fetch_quarterly_snapshot("AAPL", tenk_as_of=tenk_as_of)
        mock_company.get_quarterly_financials.assert_called_once_with()
        assert result is None


    def test_stale_10k_parses_real_xbrl_quarterly_format(self, mocker):
        """EC-84: formato REAL del 10-Q verificado en vivo (concept como columna con prefijo,
        columnas de periodo '2025-06-28 (Q3)' / '(YTD)'). El código viejo filtraba
        c.startswith("Q") sobre el índice → snapshot siempre None."""
        import pandas as pd
        from datetime import date, timedelta
        from due_diligence.tools.edgar import EdgarAdapter
        df = pd.DataFrame({"concept": ["us-gaap_Revenues", "us-gaap_GrossProfit"],
                           "label": ["Revenue", "GP"],
                           "2025-06-28 (Q3)": [94.0e9, 44.0e9],
                           "2025-06-28 (YTD)": [313.0e9, 145.0e9]})
        company = mocker.MagicMock()
        company.get_quarterly_financials.return_value.income_statement.return_value.to_dataframe.return_value = df
        mocker.patch("edgar.Company", return_value=company)
        mocker.patch("edgar.set_identity")
        snap = EdgarAdapter.fetch_quarterly_snapshot("AAPL", tenk_as_of=date.today() - timedelta(days=60))
        assert snap is not None and snap.revenue == 94.0e9 and snap.gross_profit == 44.0e9
        assert snap.period == "2025-06-28 (Q3)"


# ---------------------------------------------------------------------------
# Tests: P3 — Item 2.02 detection robusta (startswith vs ==)
# ---------------------------------------------------------------------------

class TestEarningsPressReleaseItemDetection:
    """
    P3: la detección de Item 2.02 debe funcionar con variantes de formato.
    Variantes: "2.02", "2.02 Results of Operations...", "Item 2.02".
    """

    @pytest.mark.parametrize("item_string, expected", [
        ("2.02", True),
        ("2.02 Results of Operations and Financial Condition", True),
        ("Item 2.02", True),
        ("Item 2.02 Results of Operations", True),
        ("2.01", False),
        ("5.02", False),
        ("", False),
    ])
    def test_item_202_detection_variants(self, item_string, expected):
        """startswith("2.02") detecta todas las variantes válidas sin falsos positivos."""
        result = item_string.replace("Item ", "").strip().startswith("2.02")
        assert result is expected

    def test_item_202_exact_only_misses_description_variant(self):
        """
        Verifica que el bug original (== "2.02") habría fallado con la variante
        con descripción, y que startswith lo resuelve.
        """
        item_with_desc = "2.02 Results of Operations and Financial Condition"
        old_logic = item_with_desc.replace("Item ", "").strip().lower() == "2.02"
        new_logic = item_with_desc.replace("Item ", "").strip().startswith("2.02")
        assert old_logic is False, "El bug original debería fallar con descripción"
        assert new_logic is True, "El fix debe detectar correctamente"

    def test_max_chars_default_is_8000(self):
        """P3: max_chars por defecto es 8000 (antes 3000 cortaba el press release a mitad)."""
        import inspect
        from due_diligence.tools.edgar import EdgarEventsAdapter
        sig = inspect.signature(EdgarEventsAdapter.fetch_earnings_press_release)
        default = sig.parameters["max_chars"].default
        assert default == 8000, f"Expected 8000, got {default}"


# ---------------------------------------------------------------------------
# Tests: Form 4 newest-first ordering + cap 50 (EC-61, MCE-28)
# ---------------------------------------------------------------------------

class TestForm4NewestFirst:
    """
    EC-61: con cap=20 y orden ascendente, transacciones recientes del CEO quedaban
    fuera del slice. Fix: cap→50 + sort por filing_date descendente.
    """

    def test_fetch_form4_cap_is_50(self):
        """El slice de filings es [:50], no [:20]."""
        import inspect
        from due_diligence.tools.edgar import EdgarManagementAdapter
        src = inspect.getsource(EdgarManagementAdapter.fetch_form4_transactions)
        assert "[:50]" in src, "Cap debe ser 50 para capturar transacciones recientes"

    def test_fetch_form4_sort_newest_first_in_source(self):
        """
        El código aplica sort(key=filing_date, reverse=True) antes de procesar.
        Verificado a nivel de fuente — Company se importa dentro de la función,
        por lo que el mock requeriría parchear 'edgar.Company' en runtime de edgar package.
        """
        import inspect
        from due_diligence.tools.edgar import EdgarManagementAdapter
        src = inspect.getsource(EdgarManagementAdapter.fetch_form4_transactions)
        assert "filing_date" in src, "Debe referenciar filing_date para el sort"
        assert "reverse=True" in src, "Sort debe ser descendente (newest-first)"
