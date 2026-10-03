"""
Regla 22 del Value Expert sobre ROIC incremental real (T9, EC-85 / EC-58).

Antes el contexto llamaba "marginal ROIC > average" a una simple tendencia del ROIC
(último vs más antiguo). Desde T9 el ROIC incremental (ΔNOPAT/ΔIC) se calcula de forma
determinista en parse_financials y el contexto lo muestra con su periodo; si no es
interpretable (n/m) el contexto lo dice y la regla 22 prohíbe inferir calidad de reinversión.
(Archivo separado de test_value_expert.py para no colisionar con el Bloque A.)
"""
from datetime import date

from due_diligence.schemas.base import FinancialsReport, MetricValue


def _mv(v: float, p: str, u: str = "USD") -> MetricValue:
    return MetricValue(value=v, unit=u, period=p, source="XBRL")


def _fin(**over) -> FinancialsReport:
    base = dict(
        ticker="T", cik="1", as_of_date=date(2025, 12, 31),
        revenue=[_mv(1000.0, "FY2025")], gross_profit=[], operating_income=[_mv(200.0, "FY2025")],
        net_income=[], operating_cash_flow=[], capex=[], free_cash_flow=[],
        total_assets=_mv(2000.0, "FY2025"), total_debt=_mv(300.0, "FY2025"),
        cash_and_equivalents=_mv(100.0, "FY2025"), source_filing="10-K FY2025",
        roic_series=[_mv(0.168, "FY2025", "ratio"), _mv(0.159, "FY2024", "ratio"),
                     _mv(0.12, "FY2023", "ratio"), _mv(0.10, "FY2022", "ratio")],
    )
    base.update(over)
    return FinancialsReport(**base)


def test_rule_22_is_about_trend_and_incremental_roic():
    from due_diligence.agents.value_expert import _SYSTEM_PROMPT
    assert "ROIC TREND & INCREMENTAL" in _SYSTEM_PROMPT
    assert "if incremental ROIC is n/m, say so and do not infer reinvestment quality" in _SYSTEM_PROMPT
    # Protecciones de EC-58 intactas: el label sigue siendo autoritativo
    assert 'do NOT write "declining ROIC"' in _SYSTEM_PROMPT


def test_context_shows_incremental_roic_with_period_and_average():
    from due_diligence.agents.value_expert import _format_context
    fin = _fin(incremental_roic=MetricValue(value=0.2833, unit="ratio", period="FY2022→FY2025",
                                            source="calculated"))
    ctx = _format_context({"ticker": "T", "financials": fin})
    # media de la ventana (4 puntos) = (16.8+15.9+12+10)/4 = 13.675 % → "13.7%"
    assert "Incremental ROIC (ΔNOPAT/ΔIC, FY2022→FY2025): 28.3% vs average ROIC 13.7%" in ctx
    assert "marginal ROIC > average" not in ctx   # la tendencia ya no se vende como ROIC marginal
    assert "ROIC trend interpretation (use in §5): RISING" in ctx


def test_context_says_incremental_roic_nm_when_unavailable():
    from due_diligence.agents.value_expert import _format_context
    ctx = _format_context({"ticker": "T", "financials": _fin()})
    assert "Incremental ROIC: n/m" in ctx
