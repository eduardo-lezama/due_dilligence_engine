"""
Tests de los checks deterministas del harness (H2-1 Task 2): una regla del prompt = una comprobación.

Cada check tiene tres casos: aplica y pasa, aplica y falla, no aplica (no aplica ≠ aprobado). Los
bloques de contexto usan el texto literal que emite `_format_context`. Se puntúa la salida CRUDA
del LLM: el código repara algunas reglas después y medir el informe final mediría la reparación.
"""
import pytest

from due_diligence.evals.checks import (CHECK_NAMES, check_buy_floor, check_rationale_matches_verdict,
                                        check_repairs, check_rule21_count, check_rule21_divergence_no_buy,
                                        check_rule22_roic_language, check_rule24_hypotheses,
                                        check_sections_present, run_checks, ungrounded_numbers)
from due_diligence.evals.legacy import ValueExpertLLMOutputH1 as ValueExpertLLMOutput
from due_diligence.schemas.llm_outputs import ValueExpertLLMOutput as ValueExpertLLMOutputH2
from due_diligence.schemas.reports import VerdictDecision
from due_diligence.schemas.reports import ValuationReport, ValueExpertReport

_SECTIONS = ("business_understanding", "moat_analysis", "management_assessment", "financial_quality",
             "growth_reinvestment", "valuation_section", "risks", "psychological_lens", "decision_frame")
_FILLER = "text long enough to count as a real section of the analysis."


def _raw(**over) -> ValueExpertLLMOutput:
    base = {s: f"{s} {_FILLER}" for s in _SECTIONS}
    base.update(verdict="HOLD", verdict_confidence=0.6, suggested_sizing_pct=2.0, verdict_rationale="Hold for now.")
    base.update(over)
    return ValueExpertLLMOutput(**base)


def _report(**over) -> ValueExpertReport:
    base = {s: f"{s} {_FILLER}" for s in _SECTIONS}
    base.update(ticker="TST", stub=False, verdict="HOLD", verdict_confidence=0.6, suggested_sizing_pct=2.0,
                verdict_rationale="Hold for now.")
    base.update(over)
    return ValueExpertReport(**base)


def _state(upside=None):
    return {"ticker": "TST", "valuation": ValuationReport(ticker="TST", dcf_upside_pct=upside)}


CONV = "## VALUATION METHODS CONVERGENCE (use in §6 — read Rule #21)\n- DCF: **CHEAP** | +35%\n"
ROIC_RISING = "ROIC trend interpretation (use in §5): RISING (avg ΔROIC: +2.0pp/year over 4 years)"
HYP = ("## INVESTOR'S OWN HYPOTHESES (evaluate critically, do not adopt)\n"
       "- Q: a\n  Investor: x\n- Q: b\n  Investor: y\n- Q: c\n  Investor: z\n")


def test_check_names_unique_and_complete():
    assert len(CHECK_NAMES) == len(set(CHECK_NAMES)) == 11
    assert [r.name for r in run_checks(_raw(), _report(), "", _state())] == list(CHECK_NAMES)


# --- sections_present ---
def test_sections_present_fails_on_short_section():
    r = check_sections_present(_raw(risks="Short."), _report(), "", {})
    assert r.applicable and r.passed is False and "risks" in r.detail


def test_sections_present_passes():
    assert check_sections_present(_raw(), _report(), "", {}).passed is True


# --- ungrounded_numbers ---
def test_ungrounded_flags_invented_figure():
    ctx = "Revenue: $2.9B (FY2026). DCF per share $70.06."
    assert ungrounded_numbers("Revenue $4.3B and DCF $70.06.", ctx) == ["$4.3b"]


def test_ungrounded_tolerates_rounding_years_counts_and_sections():
    ctx = "Peak margin 36.1% (FY2025). Revenue $2,912 million. Price $64.83. DCF/sh $70.06."
    text = "In FY2025 the margin peaked at 36%; revenue was $2.9B; 3 of 4 methods (§6) agree."
    assert ungrounded_numbers(text, ctx) == []


def test_ungrounded_upside_must_be_stated_in_context():
    """Los cocientes no se derivan (cubrían todo el eje): el upside real viene escrito en el contexto."""
    assert ungrounded_numbers("DCF implies 8.1% upside.", "Price $64.83. DCF/sh $70.06 (+8.1% vs price).") == []
    assert ungrounded_numbers("DCF implies 8.1% upside.", "Price $64.83. DCF/sh $70.06.") == ["8.1%"]


def test_ungrounded_keeps_sign():
    ctx = "Margin 36.1% (FY2025)."
    assert ungrounded_numbers("Margin fell to -36.1%.", ctx) == ["-36.1%"]


def test_ungrounded_check_is_continuous():
    r = run_checks(_raw(valuation_section=f"Revenue $9.9B. {_FILLER}"), _report(), "Revenue $2.9B.", _state())
    u = next(x for x in r if x.name == "ungrounded_numbers")
    assert u.applicable and u.value == 1 and u.passed is None


# --- rule21_count ---
def test_rule21_count_required_when_block_present():
    assert check_rule21_count(_raw(valuation_section=f"DCF looks cheap. {_FILLER}"), _report(), CONV, {}).passed is False
    assert check_rule21_count(_raw(valuation_section=f"2 of 4 methods signal cheap. {_FILLER}"), _report(), CONV, {}).passed


def test_rule21_count_accepts_full_tally_form():
    # Calibración con la baseline real: el modelo copia el formato del contexto (falso positivo en 13/14 casos).
    for text in ("0 cheap, 0 fair, 3 expensive out of 4 methods is a clear signal.",
                 "Convergence is 3 cheap, 0 fair, 0 expensive out of 4: DCF is cheap.",
                 "Valuation convergence is 0 cheap, 1 fair, and 2 expensive out of 4 methods."):
        assert check_rule21_count(_raw(valuation_section=f"{text} {_FILLER}"), _report(), CONV, {}).passed
    assert not check_rule21_count(_raw(valuation_section=f"3 cheap signals overall. {_FILLER}"), _report(), CONV, {}).passed


def test_rule21_count_not_applicable_when_no_method_classified():
    empty = CONV + "Convergence: 0 cheap, 0 fair, 0 expensive out of 4 methods.\n"
    assert check_rule21_count(_raw(), _report(), empty, {}).applicable is False


def test_rule21_count_not_applicable_without_block():
    r = check_rule21_count(_raw(), _report(), "no block", {})
    assert r.applicable is False and r.passed is None


# --- rule21_divergence_no_buy ---
def test_divergence_parsed_from_real_convergence_line():
    ctx = CONV + "\nConvergence: 1 cheap, 1 fair, 1 expensive out of 4 methods."
    assert check_rule21_divergence_no_buy(_raw(verdict="BUY"), _report(), ctx, {}).passed is False
    assert check_rule21_divergence_no_buy(_raw(verdict="HOLD"), _report(), ctx, {}).passed is True


def test_divergence_not_applicable_when_methods_agree():
    ctx = CONV + "\nConvergence: 3 cheap, 0 fair, 0 expensive out of 4 methods."
    assert check_rule21_divergence_no_buy(_raw(verdict="BUY"), _report(), ctx, {}).applicable is False


# --- buy_floor ---
def test_buy_floor_requires_20pct_upside():
    assert check_buy_floor(_raw(verdict="BUY"), _report(), "", _state(upside=12.0)).passed is False
    assert check_buy_floor(_raw(verdict="BUY"), _report(), "", _state(upside=35.0)).applicable is False  # BUY permitido


def test_buy_floor_applicability_comes_from_context_not_from_the_verdict():
    # Revisión final: si solo aplicara cuando el modelo dice BUY, una baseline HOLD nunca lo activaría y
    # la puerta no vería un candidato que pasa a BUY con poco upside.
    hold_low = check_buy_floor(_raw(verdict="HOLD"), _report(), "", _state(upside=12.0))
    assert hold_low.applicable and hold_low.passed is True
    assert check_buy_floor(_raw(verdict="HOLD"), _report(), "", _state(upside=35.0)).applicable is False
    assert check_buy_floor(_raw(verdict="BUY"), _report(), "", _state(upside=None)).applicable is False


# --- rule22_roic_language ---
def test_rule22_rising_forbids_declining_language():
    bad = _raw(growth_reinvestment=f"Declining ROIC shows the moat is eroding. {_FILLER}")
    assert check_rule22_roic_language(bad, _report(), ROIC_RISING, {}).passed is False
    assert check_rule22_roic_language(_raw(), _report(), ROIC_RISING, {}).passed is True


def test_rule22_not_applicable_without_label_or_with_na():
    assert check_rule22_roic_language(_raw(), _report(), "", {}).applicable is False
    na = "ROIC trend interpretation (use in §5): n/a (no ROIC data within the last 4 fiscal years to compare)"
    assert check_rule22_roic_language(_raw(), _report(), na, {}).applicable is False


def test_rule22_forbidden_phrases_still_in_prompt():
    """Guard: si el prompt cambia las frases prohibidas, el check debe revisarse."""
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    from due_diligence.evals.checks import _RULE22_FORBIDDEN
    for phrases in _RULE22_FORBIDDEN.values():
        for p in phrases:
            assert p in _SYSTEM_PROMPT.lower(), p


# --- rule24_hypotheses ---
def test_rule24_counts_labels_in_raw_output():
    one = _raw(psychological_lens=f"First: SUPPORTED. {_FILLER}")
    three = _raw(psychological_lens=f"SUPPORTED, CONTRADICTED and UNTESTABLE respectively. {_FILLER}")
    assert check_rule24_hypotheses(one, _report(), HYP, {}).passed is False
    assert check_rule24_hypotheses(three, _report(), HYP, {}).passed is True


def test_rule24_ignores_code_injected_caveat():
    raw = _raw(psychological_lens=f"The investor is optimistic. {_FILLER}")
    fixed = _report(psychological_lens="The investor is optimistic.\n⚠️ §8 did not evaluate 3 investor hypotheses "
                                       "(SUPPORTED/CONTRADICTED/UNTESTABLE)")
    assert check_rule24_hypotheses(raw, fixed, HYP, {}).passed is False


def test_rule24_not_applicable_without_block():
    assert check_rule24_hypotheses(_raw(), _report(), "", {}).applicable is False


# --- rationale_matches_verdict ---
def test_rationale_contradicting_verdict_fails():
    assert check_rationale_matches_verdict(_raw(verdict="HOLD", verdict_rationale="Clear BUY: cheap."),
                                           _report(), "", {}).passed is False
    assert check_rationale_matches_verdict(_raw(verdict="HOLD", verdict_rationale="HOLD, not a BUY yet."),
                                           _report(), "", {}).passed is True
    assert check_rationale_matches_verdict(_raw(verdict="HOLD", verdict_rationale="Wait for a better price."),
                                           _report(), "", {}).passed is True


# --- repairs ---
def test_repairs_counts_code_caveats_and_clamps():
    raw = _raw(verdict_confidence=0.9, suggested_sizing_pct=12.0)
    rep = _report(verdict_confidence=0.6, suggested_sizing_pct=6.0, risks="r\n⚠️ §7 omits governance flag: x")
    r = check_repairs(raw, rep, "", {})
    assert r.applicable and r.value == 3 and r.passed is None           # aviso + confianza + sizing


@pytest.mark.parametrize("ctx", ["", CONV])
def test_run_checks_handles_empty_state(ctx):
    assert len(run_checks(_raw(), _report(), ctx, {})) == len(CHECK_NAMES)


# --- Calibración con informes reales H1 (AAPL, BBW): falsos positivos encontrados y fijados ---
def test_ungrounded_handles_multiples_x():
    assert ungrounded_numbers("EV/EBIT 37.2x and EV/FCFF 51.3x.", "EV/EBIT: 37.2x. EV/FCFF: 51.3x.") == []


def test_ungrounded_handles_basis_points():
    ctx = "Gross margin 54.2% (Q3) vs 57.6% a year ago."
    assert ungrounded_numbers("Gross margin fell 340 basis points to 54.2%.", ctx) == []
    assert ungrounded_numbers("Gross margin fell 340 bps.", ctx) == []
    assert ungrounded_numbers("Gross margin fell 3.4 pp.", ctx) == []


def test_decision_frame_thresholds_are_not_counted():
    """§9 propone criterios de salida con cifras nuevas a propósito ("revenue below $120 million")."""
    raw = _raw(decision_frame=f"Exit if quarterly revenue falls below $120 million. {_FILLER}")
    r = next(x for x in run_checks(raw, _report(), "Revenue $2.9B.", _state()) if x.name == "ungrounded_numbers")
    assert r.value == 0


def test_ungrounded_is_not_trivially_permissive_on_real_context():
    """Adversarial (H2-1): con derivaciones de todos los pares, el 100 % de porcentajes aleatorios
    "tenía respaldo" en el contexto real de CALM — el check no medía nada. Tolerancia por precisión
    escrita + derivaciones solo entre importes en $ ⇒ la mayoría de cifras inventadas se detectan."""
    import random
    from pathlib import Path
    ctx = (Path(__file__).parent / "evals" / "cases" / "CALM-base.md").read_text(encoding="utf-8")
    rng = random.Random(1)
    pcts = [f"{round(rng.uniform(-50, 100), 1)}%" for _ in range(200)]
    usd = [f"${round(rng.uniform(1, 500), 1)}" for _ in range(200)]
    accepted_pct = sum(not ungrounded_numbers(f"value {t} here", ctx) for t in pcts) / len(pcts)
    accepted_usd = sum(not ungrounded_numbers(f"value {t} here", ctx) for t in usd) / len(usd)
    assert accepted_pct < 0.25 and accepted_usd < 0.15, (accepted_pct, accepted_usd)


def test_rationale_negated_or_conditional_mentions_do_not_count():
    """Calibración con gpt-6.1-sol: nombrar otro veredicto para descartarlo no es concluirlo."""
    for text in ("CALM lacks the margin of safety needed for BUY.",
                 "Strong cash generation argues against SELL, but the conflict remains.",
                 "I favor EPV as the anchor and withhold a BUY.",
                 "Favorable evidence warrants patience rather than a SELL, while risks prevent a conviction BUY."):
        assert check_rationale_matches_verdict(_raw(verdict="HOLD", verdict_rationale=text), _report(), "", {}).passed, text
    assert not check_rationale_matches_verdict(_raw(verdict="HOLD", verdict_rationale="This is a clear BUY."),
                                               _report(), "", {}).passed


def test_ungrounded_numbers_understands_percent_word():
    from due_diligence.evals.checks import ungrounded_numbers
    ctx = "Revenue growth: 16.0%\n"
    assert ungrounded_numbers("revenue grew 16 percent", ctx) == []
    assert ungrounded_numbers("revenue grew 23 percent", ctx) == ["23 percent"]


def test_rationale_negated_sentences_from_real_sol_outputs():
    """Frases reales de gpt-6.1-sol (run completo) que concluyen HOLD descartando otro veredicto."""
    for text in ("Positive earnings do not yet establish a definitive SELL case.",
                 "Moat durability and sustainable earning power remain insufficiently resolved for BUY.",
                 "Three cheap signals argue against selling, but the conflict is unresolved; HOLD is not a SELL."):
        assert check_rationale_matches_verdict(_raw(verdict="HOLD", verdict_rationale=text), _report(), "", {}).passed, text


def test_rule22_falling_contrast_with_other_metric_is_not_a_violation():
    ctx = "ROIC trend interpretation (use in §5): FALLING (avg ΔROIC: -2.0pp/year over 4 years)"
    ok = "The second conflict is falling annual ROIC versus improving recent operating performance."
    assert check_rule22_roic_language(_raw(financial_quality=f"{ok} {_FILLER}"), _report(), ctx, {}).passed
    bad = "ROIC is improving steadily as the business scales."
    assert not check_rule22_roic_language(_raw(financial_quality=f"{bad} {_FILLER}"), _report(), ctx, {}).passed


# --- H2-2 (ADR-041): checks de la taxonomía nueva ---
def _raw_h2(**over) -> ValueExpertLLMOutputH2:
    base = {s: f"{s} {_FILLER}" for s in _SECTIONS}
    base.update(verdict="BUY", business_class="average", confidence_adjustment=0.0, verdict_rationale="BUY now.")
    base.update(over)
    return ValueExpertLLMOutputH2(**base)


def _decision(**over) -> VerdictDecision:
    base = dict(verdict="WATCHLIST", verdict_raw="BUY", business_class_llm="average", business_class="average",
                required_mos=0.5, anchor_value=45.6, anchor_source="EPV", entry_price=22.8, price=25.53)
    base.update(over)
    return VerdictDecision(**base)


def test_buy_mos_floor_flags_buy_above_entry_price():
    from due_diligence.evals.checks import check_buy_mos_floor
    rep = _report(decision=_decision())
    assert check_buy_mos_floor(_raw_h2(verdict="BUY"), rep, "", {}).passed is False
    assert check_buy_mos_floor(_raw_h2(verdict="WATCHLIST"), rep, "", {}).passed is True    # aplica igualmente
    assert check_buy_mos_floor(_raw_h2(), _report(decision=_decision(price=20.0)), "", {}).passed is True
    assert check_buy_mos_floor(_raw_h2(), _report(), "", {}).applicable is False             # informe H1


def test_business_class_consistency_and_repairs_count_downgrades():
    from due_diligence.evals.checks import check_business_class_consistent
    hardened = _report(decision=_decision(business_class_llm="quality_compounder"))
    assert check_business_class_consistent(_raw_h2(), hardened, "", {}).passed is False
    assert check_repairs(_raw_h2(), _report(decision=_decision()), "", {}).value == 1.0     # BUY → WATCHLIST


def test_rationale_check_knows_the_new_labels():
    bad = _raw_h2(verdict="PASS", verdict_rationale="This is a clear BUY.")
    assert check_rationale_matches_verdict(bad, _report(), "", {}).passed is False
    ok = _raw_h2(verdict="WATCHLIST", verdict_rationale="WATCHLIST until the price reaches the entry level.")
    assert check_rationale_matches_verdict(ok, _report(), "", {}).passed is True
