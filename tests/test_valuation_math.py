"""
Tests de valuation_math (ADR-033): FCFF, tipo impositivo, bridge, nº de acciones, DCF y WACC.

Toda la aritmética esperada está calculada a mano en el comentario de cada test; si un
número cambia, recalcular y dejar la cuenta escrita (regla de tests del proyecto).
Funciones puras → cero I/O, cero fixtures de red.
"""
import pytest

from due_diligence.agents.valuation_math import (
    MARGINAL_TAX_RATE, ValuationInputError, compute_fcff, fcff_breakdown, equity_bridge,
    net_claims, resolve_share_count, dcf_components, validate_dcf_rates, per_share,
)
from due_diligence.tools.xbrl_extract import effective_tax_rate_multi


# --- tipo impositivo canónico (una sola función para FCFF, ROIC, EPV) ---
def test_effective_tax_rate_multi_aggregates_three_years():
    # Σtax/Σpretax = (40+15+30)/(200+150+100) = 85/450 = 0.1889 — suaviza un año con crédito fiscal
    assert effective_tax_rate_multi([40.0, 15.0, 30.0], [200.0, 150.0, 100.0]) == pytest.approx(85 / 450)


def test_effective_tax_rate_multi_prdo_like_interest_income_not_a_tax():
    # EBIT 120, intereses cobrados 31.5 → pretax 151.5, tax 37.875: t = 0.25 (la fórmula NI/EBIT daría 5 %)
    assert effective_tax_rate_multi([37.875], [151.5]) == pytest.approx(0.25)


def test_effective_tax_rate_multi_defaults_and_clamp():
    assert effective_tax_rate_multi([], []) == pytest.approx(0.25)
    assert effective_tax_rate_multi([10.0], [-5.0]) == pytest.approx(0.25)     # pretax ≤ 0 → default
    assert effective_tax_rate_multi([80.0], [100.0]) == pytest.approx(0.5)     # clamp
    assert MARGINAL_TAX_RATE == 0.25


def test_valuation_math_reexports_canonical_tax_functions():
    """Un hecho = un lugar: valuation_math re-exporta, no redefine (revisión adversarial)."""
    from due_diligence.agents import valuation_math
    from due_diligence.tools import xbrl_extract
    assert valuation_math.effective_tax_rate_multi is xbrl_extract.effective_tax_rate_multi
    assert valuation_math.effective_tax_rate is xbrl_extract.effective_tax_rate


# --- FCFF ---
def test_fcff_levered_company_example_a():
    # 260 + 40×0.75 − 60 − 20 = 210
    assert compute_fcff(260e6, 60e6, 40e6, 0.25, 20e6) == pytest.approx(210e6)


def test_fcff_sbc_flag_off_adds_back():
    # 260 + 30 − 60 = 230 (SBC no tratado como coste)
    assert compute_fcff(260e6, 60e6, 40e6, 0.25, 20e6, treat_sbc_as_cash_cost=False) == pytest.approx(230e6)


def test_fcff_strips_after_tax_interest_income_prdo_like():
    # 150 − 5 − 10 − 31.5×0.75 (=23.625) = 111.375
    bd = fcff_breakdown(150e6, 5e6, 0.0, 0.25, 10e6, interest_income=31.5e6)
    assert bd.fcff == pytest.approx(111.375e6)
    assert bd.after_tax_interest_income == pytest.approx(23.625e6)


def test_fcff_subtracts_finance_lease_principal_as_capex_equivalent():
    # CFO 1000, capex 300, principal de leasing financiero 150 → 550 (no 700): el ROU financiero
    # es alta no-caja y su principal va a financiación, así que CFO − capex no lo descuenta nunca.
    assert compute_fcff(1000e6, 300e6, 0.0, 0.25, 0.0, finance_lease_principal=150e6) == pytest.approx(550e6)
    bd = fcff_breakdown(1000e6, 300e6, 0.0, 0.25, 0.0, finance_lease_principal=150e6)
    assert bd.finance_lease_principal == pytest.approx(150e6)


@pytest.mark.parametrize("field", ["capex", "interest_expense", "sbc"])
def test_fcff_negative_inputs_fail_loud(field):
    kwargs = dict(ocf=100e6, capex=10e6, interest_expense=5e6, tax_rate=0.25, sbc=1e6)
    kwargs[field] = -1.0  # error de convención de signo en el parser → explotar, no maquillar
    with pytest.raises(ValuationInputError):
        compute_fcff(**kwargs)


# --- bridge ---
def test_bridge_levered_example_a():
    # 500 − 50 = 450; 2 333.33 − 450 = 1 883.33
    b = equity_bridge(2_333.333333e6, total_debt=500e6, cash=50e6)
    assert b.net_claims == pytest.approx(450e6)
    assert b.equity_value == pytest.approx(1_883.333333e6)
    assert set(b.components_missing) == {
        "short_term_investments", "long_term_investments", "minority_interest", "preferred_stock"}


def test_bridge_azo_like_leases_memo_not_deducted():
    # FCFF 2 400 / 9 % = 26 666.67; net claims 9 000 − 300 = 8 700; leases 3 500 solo memo
    ev = 2_400e6 / 0.09
    b = equity_bridge(ev, total_debt=9_000e6, cash=300e6, sti=0.0, lt_investments=0.0,
                      lease_liabilities=3_500e6, minority_interest=0.0, preferred=0.0)
    assert b.equity_value == pytest.approx(ev - 8_700e6)
    assert b.operating_lease_liabilities_memo == pytest.approx(3_500e6)
    assert b.equity_value > ev - 12_200e6                           # NO se resta otra vez


def test_bridge_prdo_like_net_cash_raises_equity():
    # net claims = 0 − 400 − 300 − 100 = −800; equity 1 237.5 + 800 = 2 037.5; /65 = 31.346
    b = equity_bridge(1_237.5e6, total_debt=0.0, cash=400e6, sti=300e6, lt_investments=100e6)
    assert b.net_claims == pytest.approx(-800e6)
    assert per_share(b.equity_value, 65e6) == pytest.approx(31.346, abs=1e-3)


def test_bridge_minority_and_preferred_reduce_equity():
    # 100 + 5 + 3 − 10 = 98
    assert net_claims(100.0, 10.0, minority_interest=5.0, preferred=3.0) == pytest.approx(98.0)


def test_bridge_negative_equity_is_reported_not_clamped():
    assert equity_bridge(100e6, total_debt=300e6, cash=0.0).equity_value == pytest.approx(-200e6)


def test_per_share_zero_shares_fail_loud():
    with pytest.raises(ValuationInputError):
        per_share(1e9, 0.0)


# --- nº de acciones ---
def test_share_count_applies_dilution_ratio_to_point_in_time_azo_like():
    n, src = resolve_share_count(16.8e6, diluted_weighted=17.6e6, basic_weighted=17.2e6)
    assert n == pytest.approx(16.8e6 * 17.6 / 17.2)
    assert "diluted/basic" in src


def test_share_count_fallbacks():
    assert resolve_share_count(10e6, None, None)[0] == pytest.approx(10e6)
    assert resolve_share_count(None, 11e6, None)[0] == pytest.approx(11e6)
    assert resolve_share_count(None, None, None, market_cap=1.5e9, price=150.0)[0] == pytest.approx(10e6)
    assert resolve_share_count(None, None, None) == (None, "unavailable")


# --- DCF ---
def test_validate_rates_fail_loud():
    with pytest.raises(ValuationInputError):
        validate_dcf_rates(0.09, 0.09)
    with pytest.raises(ValuationInputError):
        validate_dcf_rates(0.03, 0.025)     # spread 0.5pp < 1pp
    validate_dcf_rates(0.09, 0.025)


def test_validate_rates_one_pp_spread_exactly_is_ok():
    validate_dcf_rates(0.09, 0.08)          # 0.09 − 0.08 = 0.00999… en coma flotante: debe aceptarse


def test_dcf_zero_growth_equals_perpetuity_without_reinvestment_model():
    # g = 0 en todas las etapas → EV = FCFF / WACC = 210 / 0.09 = 2 333.33
    c = dcf_components(210e6, 0.0, 0.0, 0.09, stage1_years=5, fade_years=5)
    assert c.enterprise_value == pytest.approx(210e6 / 0.09)
    assert c.terminal_reinvestment_modeled is False


def test_dcf_terminal_reinvestment_example_a():
    # Explícito: 210 × anualidad(9 %, 10) = 210 × 6.417658 = 1 347.708
    # Terminal: NOPAT 225 × (1+0) × (1 − 0/0.09) / 0.09 = 2 500 → PV = 2 500 × 0.4224108 = 1 056.027
    # EV = 1 347.708 + 1 056.027 = 2 403.735
    c = dcf_components(210e6, 0.0, 0.0, 0.09, stage1_years=5, fade_years=5, nopat_base=225e6)
    assert c.enterprise_value == pytest.approx(2_403.735e6, rel=1e-5)
    assert c.terminal_value_share == pytest.approx(1_056.027 / 2_403.735, rel=1e-4)
    assert c.terminal_reinvestment_modeled is True


def test_dcf_terminal_reinvestment_reduces_tv_when_growth_positive():
    # NOPAT 100, FCFF 90, WACC 9 %, g 2.5 %: sin reinversión TV flujo = 90×1.025 = 92.25;
    # con RONIC = WACC la reinversión necesaria es g/RONIC = 27.8 % → flujo = 102.5 × 0.7222 = 74.03
    naive = dcf_components(90e6, 0.025, 0.025, 0.09, stage1_years=1)
    modeled = dcf_components(90e6, 0.025, 0.025, 0.09, stage1_years=1, nopat_base=100e6)
    assert modeled.pv_terminal < naive.pv_terminal
    assert modeled.pv_terminal / naive.pv_terminal == pytest.approx((102.5 * (1 - 0.025 / 0.09)) / 92.25, rel=1e-6)


def test_terminal_value_share_exact():
    # explícito 100/1.1 = 90.909; TV = 1000 en t=1 → 909.09; share = 909.09/1000 = 10/11
    c = dcf_components(100e6, 0.0, 0.0, 0.10, stage1_years=1)
    assert c.terminal_value_share == pytest.approx(10 / 11)


def test_mid_year_factor():
    end = dcf_components(100e6, 0.0, 0.0, 0.09, stage1_years=1)
    mid = dcf_components(100e6, 0.0, 0.0, 0.09, stage1_years=1, mid_year=True)
    assert mid.enterprise_value == pytest.approx(end.enterprise_value * 1.09 ** 0.5)


# --- WACC real y Kd por rating sintético (T11) ---
from due_diligence.agents.valuation_math import compute_wacc, describe_wacc, estimate_pre_tax_cost_of_debt  # noqa: E402


def test_compute_wacc_hand_example():
    # 0.75×9.2% + 0.25×6.0%×0.75 = 6.9% + 1.125% = 8.025%
    w = compute_wacc(0.092, 0.06, 0.25, equity_value=1_500e6, debt_value=500e6)
    assert w.wacc == pytest.approx(0.08025)
    assert w.equity_weight == pytest.approx(0.75)
    assert w.after_tax_cost_of_debt == pytest.approx(0.045)


def test_compute_wacc_no_debt_equals_ke():
    assert compute_wacc(0.107, 0.062, 0.25, 1e9, 0.0).wacc == pytest.approx(0.107)


def test_compute_wacc_non_positive_equity_fails_loud():
    with pytest.raises(ValuationInputError):
        compute_wacc(0.09, 0.06, 0.25, equity_value=0.0, debt_value=1e9)


def test_cost_of_debt_synthetic_rating_from_coverage():
    # Rating sintético (Damodaran): cobertura EBIT/intereses → spread sobre Rf. 3 825/425 = 9.0× → 0.6 %
    kd, src = estimate_pre_tax_cost_of_debt(ebit=3.825e9, interest_expense=0.425e9)
    assert kd == pytest.approx(0.042 + 0.006) and "coverage 9.0x" in src


def test_cost_of_debt_low_coverage_high_spread_and_fallbacks():
    # 100/100 = 1.0× → tramo [0.80, 1.25) "CCC" → 8.0 %. (El plan usaba 100/80 = 1.25×, que cae
    # justo en el suelo del tramo B- = 6.5 % de su propia tabla: dato del test corregido, tabla intacta.)
    kd, _ = estimate_pre_tax_cost_of_debt(ebit=100e6, interest_expense=100e6)
    assert kd == pytest.approx(0.042 + 0.08)
    kd, _ = estimate_pre_tax_cost_of_debt(ebit=100e6, interest_expense=80e6)       # 1.25× → B- 6.5 %
    assert kd == pytest.approx(0.042 + 0.065)
    kd, src = estimate_pre_tax_cost_of_debt(ebit=None, interest_expense=None)     # sin datos → Rf + 2 pp
    assert kd == pytest.approx(0.062) and "fallback" in src
    kd, src = estimate_pre_tax_cost_of_debt(ebit=-50e6, interest_expense=10e6)    # EBIT ≤ 0 → peor tramo
    assert kd == pytest.approx(0.042 + 0.17) and "coverage <0.2x" in src


def test_describe_wacc_is_human_readable():
    # Ke 9.2 % × 75 % + Kd 6.0 % × (1−25 %) × 25 % = 8.0 %
    s = describe_wacc(compute_wacc(0.092, 0.06, 0.25, 1_500e6, 500e6,
                                   cost_of_equity_source="CAPM", cost_of_debt_source="synthetic A+"))
    assert "Ke 9.2%" in s and "× 75%" in s and "Kd 6.0%" in s and "(1−25%)" in s and "= 8.0%" in s
