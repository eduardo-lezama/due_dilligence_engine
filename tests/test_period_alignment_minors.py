"""
Menores de la revisión final de H1 (#9, #10): series emparejadas por POSICIÓN en vez de por año.

Mismo patrón que EC-82/EC-83: con un año ausente en una serie, el índice i deja de ser "i años
atrás" y se mezclan ejercicios distintos.
"""
from due_diligence.agents.critic import _detect_red_flags
from due_diligence.agents.value_expert import _format_context
from due_diligence.schemas.base import FinancialsReport, MetricValue


def _mv(v, p, unit="USD"):
    return MetricValue(value=v, unit=unit, period=p, source="test")


def _fin(**over):
    base = dict(ticker="TST", cik="1", as_of_date="2025-12-31", source_filing="10-K FY2025",
                revenue=[_mv(1000.0, "FY2025")], gross_profit=[_mv(400.0, "FY2025")],
                operating_income=[_mv(100.0, "FY2025")], net_income=[_mv(80.0, "FY2025")],
                operating_cash_flow=[_mv(120.0, "FY2025")], capex=[_mv(20.0, "FY2025")],
                free_cash_flow=[_mv(100.0, "FY2025")], total_assets=_mv(2000.0, "FY2025"),
                total_debt=_mv(0.0, "FY2025"), cash_and_equivalents=_mv(0.0, "FY2025"))
    base.update(over)
    return FinancialsReport(**base)


def test_roic_trend_delta_uses_fiscal_years_not_positions():
    # FY2024 ausente: de 10 % (FY2023) a 20 % (FY2025) son +5 pp/año en 2 años, no +10 pp en 1.
    fin = _fin(roic_series=[_mv(0.20, "FY2025", "ratio"), _mv(0.10, "FY2023", "ratio")])
    ctx = _format_context({"ticker": "TST", "financials": fin})
    assert "RISING (avg ΔROIC: +5.0pp/year over 2 years)" in ctx


def test_capital_return_flag_compares_same_fiscal_year():
    # Recompras solo de FY2024: no se pueden comparar con el FCF de FY2025 (antes: 90 % → flag).
    misaligned = _fin(buybacks=[_mv(90.0, "FY2024")])
    assert not any("HIGH CAPITAL RETURN" in f for f in _detect_red_flags({"ticker": "TST", "financials": misaligned}))
    aligned = _fin(buybacks=[_mv(90.0, "FY2025")])
    assert any("HIGH CAPITAL RETURN (90% of FCF)" in f for f in _detect_red_flags({"ticker": "TST", "financials": aligned}))


def test_moat_buyback_criterion_compares_same_fiscal_year():
    from due_diligence.agents.competitive import _check_wide_moat_criteria
    misaligned = _fin(buybacks=[_mv(90.0, "FY2024")])
    assert not any("Buybacks" in f for f in _check_wide_moat_criteria(misaligned))
    aligned = _fin(buybacks=[_mv(90.0, "FY2025")])
    assert any("Buybacks 90% of FCF" in f for f in _check_wide_moat_criteria(aligned))


def test_expert_capital_returned_uses_fcf_year():
    # Recompras FY2024 y FCF FY2025: no hay % de FCF del mismo año → no se muestra un % mezclado.
    ctx = _format_context({"ticker": "TST", "financials": _fin(buybacks=[_mv(90.0, "FY2024")])})
    assert "% of FCF" not in ctx.split("Capital returned")[1].split("\n")[0] if "Capital returned" in ctx else True
    ctx2 = _format_context({"ticker": "TST", "financials": _fin(buybacks=[_mv(90.0, "FY2025")])})
    assert "Capital returned to shareholders (FY2025): $90 (buybacks $90) = 90% of FCF" in ctx2


def test_value_at_helper():
    from due_diligence.utils.periods import value_at
    series = [_mv(1.0, "FY2025"), _mv(2.0, "FY2023")]
    assert value_at(series, "FY2023") == 2.0 and value_at(series, "FY2024") is None


def test_roic_trend_not_computed_when_no_point_within_four_years():
    # AAPL real (dataset H2-1): ROIC n/m en FY2018-FY2024 (IC < 5 % de activos) → solo FY2025 y FY2017.
    # Antes se comparaba FY2025 con FY2017 como "tendencia a 8 años" (bug de EC-96).
    fin = _fin(roic_series=[_mv(2.725, "FY2025", "ratio"), _mv(0.218, "FY2017", "ratio")])
    ctx = _format_context({"ticker": "TST", "financials": fin})
    assert "ROIC trend interpretation (use in §5): n/a" in ctx
    assert "over 8 years" not in ctx
