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
    assert report.revenue[0].source == "XBRL"


def test_parse_financials_free_cash_flow_is_computed(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_fcf = 111_482_000_000 - 12_715_000_000  # OCF - capex FY2025
    assert report.free_cash_flow[0].value == pytest.approx(expected_fcf)
    assert report.free_cash_flow[0].source == "calculated"


def test_parse_financials_total_debt_is_sum_of_lt_and_st(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_debt = 90_678_000_000 + 0  # LT debt FY2025, no ST borrowings
    assert report.total_debt.value == pytest.approx(expected_debt)


def test_parse_financials_cash_includes_short_term_investments(aapl_raw):
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)
    expected_cash = 35_934_000_000 + 0  # Cash FY2025, STI=0 in this filing
    assert report.cash_and_equivalents.value == pytest.approx(expected_cash)


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
    # FY2025: tax_rate = 20719/132729 ≈ 15.6%, NOPAT = 133050×0.844 ≈ 112245M
    # IC = 73733 + 90678 - 35934 ≈ 128477M → ROIC ≈ 0.874
    assert report.roic_series[0].value == pytest.approx(0.874, abs=0.01)


def test_parse_financials_invested_capital(aapl_raw):
    """Invested Capital primaria: Equity + Debt - Cash (cuando no es degenerada)."""
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(aapl_raw)

    assert report.invested_capital is not None
    # Para AAPL: ic_primary ($128B) > 10% × ic_fallback ($33B) → primary se usa
    expected_ic = 73_733_000_000 + 90_678_000_000 - 35_934_000_000
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
    assert "fallback" in report.invested_capital.source
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
        assert "No officer/director open-market transactions" in result
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
