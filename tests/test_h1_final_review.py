"""
Tests de la revisión final de la rama H1 (hallazgos #1–#8 de la review de cierre).

Cada test reproduce un hallazgo concreto de la revisión de toda la rama: inputs ausentes que
contaban 0 en silencio, tipo impositivo sin procedencia, tipos mezclados en el valor terminal,
cobertura de intereses de años distintos, división por EBIT cero, equity ausente tratado como
negativo, etiquetas del rating sintético y citas que casaban cruzando dos fuentes.
"""
from datetime import date

import pytest

from due_diligence.agents.valuation import suggest_wacc, valuation_agent
from due_diligence.agents.valuation_math import dcf_components, estimate_pre_tax_cost_of_debt
from due_diligence.schemas.base import Citation
from due_diligence.tools import xbrl_extract as xe
from due_diligence.tools.edgar import EdgarAdapter
from due_diligence.validation.citations import validate_citations

from tests.test_edgar import _raw_with_history
from tests.test_valuation import _levered_financials, _levered_state, _mv
from tests.test_xbrl_extract import FakeFacts, _min_frames


def _state_with(**fin_updates):
    st = _levered_state()
    st["financials"] = _levered_financials().model_copy(update=fin_updates)
    return st


# --- #1: un input del FCFF ausente en UN año no puede contar 0 en silencio ---
def test_fcff_input_missing_in_one_year_is_reported():
    st = _state_with(
        revenue=[_mv(2_000e6), _mv(1_800e6, "FY2024"), _mv(1_600e6, "FY2023")],
        operating_cash_flow=[_mv(260e6), _mv(200e6, "FY2024"), _mv(150e6, "FY2023")],
        capex=[_mv(60e6), _mv(40e6, "FY2023")],                      # falta FY2024
        interest_expense=[_mv(40e6), _mv(40e6, "FY2024"), _mv(40e6, "FY2023")],
        sbc=[_mv(20e6), _mv(18e6, "FY2024"), _mv(16e6, "FY2023")],
    )
    r = valuation_agent(st)["valuation"]
    assert "capex FY2024" in r.dcf_reliability_note


def test_sbc_missing_only_in_latest_year_triggers_sbc_note():
    st = _state_with(sbc=[_mv(18e6, "FY2024")])                     # serie no vacía, pero sin FY2025
    r = valuation_agent(st)["valuation"]
    assert "sbc" in r.fcff_breakdown.missing_inputs
    assert "SBC unavailable" in r.dcf_reliability_note


# --- #2: el tipo impositivo lleva su procedencia ---
def test_tax_rate_default_is_labelled_and_noted():
    r = valuation_agent(_levered_state())["valuation"]               # sin income_tax / pretax
    assert r.tax_rate_used == pytest.approx(0.25)
    assert "default" in r.tax_rate_source
    assert "Tax rate" in r.dcf_reliability_note


def test_tax_rate_source_effective_when_data_present():
    r = valuation_agent(_state_with(income_tax=[_mv(20e6)], pretax_income=[_mv(100e6)]))["valuation"]
    assert r.tax_rate_used == pytest.approx(0.20)
    assert r.tax_rate_source.startswith("effective")


def test_pretax_chain_includes_equity_method_variant():
    inc, bs, cf = _min_frames()
    inc.loc["IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"] = [
        "p", False, 50.0, 40.0]
    raw = xe.build_raw(ticker="x", cik="1", period_end=date(2025, 9, 27), inc_df=inc, bs_df=bs,
                       cf_df=cf, facts=FakeFacts(), shares_outstanding=10.0)
    assert [i["value"] for i in raw["annual"]["pretax_income"]] == [50.0, 40.0]


# --- #3: el valor terminal usa el tipo MARGINAL, no el efectivo ---
def test_terminal_nopat_uses_marginal_tax_rate():
    # Efectivo 10 % → FCFF = 260 + 40×0.9 − 60 − 20 = 216; NOPAT terminal = 300 × (1 − 25 %) = 225
    r = valuation_agent(_state_with(income_tax=[_mv(10e6)], pretax_income=[_mv(100e6)]))["valuation"]
    expected = dcf_components(216e6, 0.0, 0.0, 0.09, stage1_years=5, fade_years=5, nopat_base=225e6)
    assert r.enterprise_value_dcf.value == pytest.approx(expected.enterprise_value, rel=1e-6)


# --- #4: la cobertura de intereses empareja EBIT e intereses del MISMO año ---
def test_suggest_wacc_coverage_requires_same_period():
    fin = _levered_financials().model_copy(update={"interest_expense": [_mv(40e6, "FY2024")]})
    w = suggest_wacc(fin, 1_500e6)
    assert "fallback" in w.cost_of_debt_source


# --- #5: EV/EBIT con EBIT ≤ 0 no lanza ni da un múltiplo negativo ---
@pytest.mark.parametrize("ebit", [0.0, -50e6])
def test_ev_ebit_not_computed_for_non_positive_ebit(ebit):
    r = valuation_agent(_state_with(operating_income=[_mv(ebit)]))["valuation"]
    assert r.ev_ebit_multiple is None


# --- #6: equity AUSENTE en un año no es equity negativo ---
def test_missing_equity_year_does_not_force_operating_ic():
    raw = _raw_with_history()
    raw["balance_history"][-1]["stockholders_equity"] = None          # FY2022 sin dato
    r = EdgarAdapter.parse_financials(raw)
    # IC primario: FY2025 = 800+300−100 = 1000, FY2024 = 900 → media 950; NOPAT 160
    assert r.roic_series[0].value == pytest.approx(160 / 950, abs=1e-4)


# --- #7: etiquetas del rating sintético (tabla Damodaran large firms) ---
@pytest.mark.parametrize("coverage,label", [(9.0, "AAA"), (7.0, "AA"), (6.0, "A+"), (5.0, "A"),
                                            (3.5, "A-"), (1.3, "B-"), (1.0, "CCC"), (0.7, "CC")])
def test_synthetic_rating_labels(coverage, label):
    _, src = estimate_pre_tax_cost_of_debt(ebit=coverage * 100e6, interest_expense=100e6)
    assert src.startswith(f"synthetic rating {label} (")


# --- #8: una cita no puede casar cruzando la frontera entre dos fuentes ---
def test_citation_cannot_span_two_sources():
    corpus = {"10-K Item 7": "Management noted that the company reported revenue",
              "Web (Tavily)": "growth of 12% in fiscal 2025 according to analysts."}
    [c] = validate_citations([Citation(claim="c", source_url="10-K",
                                       excerpt="the company reported revenue growth of 12% in fiscal 2025",
                                       confidence=0.9)], corpus)
    assert c.verified is False


def test_citation_within_second_source_still_verified():
    corpus = {"10-K Item 7": "Management noted that the company reported revenue",
              "Web (Tavily)": "growth of 12% in fiscal 2025 according to analysts."}
    [c] = validate_citations([Citation(claim="c", source_url="web",
                                       excerpt="growth of 12% in fiscal 2025 according to analysts",
                                       confidence=0.9)], corpus)
    assert c.verified is True
