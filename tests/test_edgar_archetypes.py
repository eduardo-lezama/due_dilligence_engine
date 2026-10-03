"""
Tests críticos del parser XBRL parametrizados sobre los 4 arquetipos (MCE-25).

Por qué existen estos tests:
    test_edgar.py cubre el happy path de AAPL exhaustivamente.
    Pero varios bugs históricos (EC-14, EC-18, EC-21) surgieron porque el sistema
    asumía propiedades que solo se cumplen en AAPL (equity positivo enorme,
    revenue en miles de millones, capex siempre presente).

    Estos tests corren contra los 4 arquetipos definidos en conftest.py:
        aapl  — large cap ideal (baseline)
        bbw   — small cap, revenue en millones
        azo   — buyback-heavy, equity negativo
        prdo  — asset-light, capex ausente en XBRL

    Si un test falla para un arquetipo específico, es un bug real — documentar
    en EDGE_CASES.md y arreglar antes de declarar el fix completo (MCE-26).

Scope: solo EdgarAdapter.parse_financials — la capa determinista.
Los agentes LLM se testean con mocks; el feedback loop ahí es el external audit.
"""

import pytest


# ---------------------------------------------------------------------------
# Test 1: el parser no crashea y devuelve FinancialsReport válido
# ---------------------------------------------------------------------------

def test_parse_financials_valid_report(archetype_raw):
    """
    Smoke test fundamental: parse_financials devuelve FinancialsReport
    para cualquier arquetipo, sin excepción.
    """
    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.schemas.base import FinancialsReport

    report = EdgarAdapter.parse_financials(archetype_raw)

    assert isinstance(report, FinancialsReport)
    assert report.ticker == archetype_raw["ticker"]


# ---------------------------------------------------------------------------
# Test 2: revenue nunca vacío (EC-14 raíz — si revenue falla, todo falla)
# ---------------------------------------------------------------------------

def test_revenue_series_not_empty(archetype_raw):
    """
    Propiedad fundamental: cualquier empresa con 10-K debe tener revenue extraído.
    Si revenue está vacío, el análisis completo falla silenciosamente — el LLM
    recibe contexto vacío y alucina cifras.
    """
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(archetype_raw)

    assert len(report.revenue) >= 1, (
        f"revenue vacío para {archetype_raw['ticker']} — "
        "la extracción XBRL falló completamente"
    )
    assert report.revenue[0].value > 0


# ---------------------------------------------------------------------------
# Test 3: FCF no formatea como $0.0B cuando el valor real es > $1M (EC-14)
# ---------------------------------------------------------------------------

def test_fcf_not_formatted_as_zero_billions(archetype_raw):
    """
    EC-14: para empresas con FCF real > $1M, fmt_usd no debe producir '$0.0B'.
    El formatter debe adaptar la unidad por magnitud (M/B/k).

    Caso real: BBW tiene FCF ~$39M. Antes del fix producía '$0.0B' porque
    el código hacía value/1e9 con formato .1f — resultando en 0.0 para small caps.
    """
    from due_diligence.tools.edgar import EdgarAdapter
    from due_diligence.utils.formatting import fmt_usd

    report = EdgarAdapter.parse_financials(archetype_raw)

    if not report.free_cash_flow:
        pytest.skip(f"{archetype_raw['ticker']}: FCF no disponible, no aplica")

    fcf_value = report.free_cash_flow[0].value
    if abs(fcf_value) < 1_000_000:
        pytest.skip(f"{archetype_raw['ticker']}: FCF real < $1M, '$0.0B' podría ser correcto")

    formatted = fmt_usd(fcf_value)

    assert formatted != "$0.0B", (
        f"FCF de ${fcf_value:,.0f} para {archetype_raw['ticker']} se formateó como '$0.0B' — "
        "EC-14 regresionó"
    )


# ---------------------------------------------------------------------------
# Test 4: invested_capital nunca None para empresa operativa (EC-18)
# ---------------------------------------------------------------------------

def test_invested_capital_never_none(archetype_raw):
    """
    EC-18: invested_capital nunca debe ser None para una empresa operativa.

    AZO tiene stockholders_equity = -$3.4B por buybacks agresivos.
    La fórmula primaria (equity + debt - cash) produce un valor degenerado.
    El sistema debe activar el fallback (total_assets - cash) y devolver IC válido.

    Sin este test, el fallback solo estaba verificado con una mutación artificial
    del fixture AAPL, no con datos reales de AZO.
    """
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(archetype_raw)

    assert report.invested_capital is not None, (
        f"invested_capital es None para {archetype_raw['ticker']} — "
        "el fallback assets-cash no se activó correctamente"
    )
    assert report.invested_capital.value > 0, (
        f"invested_capital <= 0 para {archetype_raw['ticker']}: "
        f"{report.invested_capital.value}"
    )


# ---------------------------------------------------------------------------
# Test 5: ROIC disponible o gracefully unavailable — nunca crash (EC-21)
# ---------------------------------------------------------------------------

def test_roic_available_or_graceful(archetype_raw):
    """
    EC-21: el parser debe producir ROIC cuando los datos lo permiten,
    o dejar roic_series=[] sin crashear. Nunca excepción.

    PRDO puede no tener EBIT en XBRL — roic_series=[] es aceptable.
    AZO tiene equity negativo → ROIC debe calcularse con IC fallback.
    Si hay ROIC, el valor debe ser un ratio razonable (no infinito ni absurdo).
    """
    from due_diligence.tools.edgar import EdgarAdapter

    report = EdgarAdapter.parse_financials(archetype_raw)

    # Nunca debe lanzar excepción — roic_series=[] es aceptable
    assert isinstance(report.roic_series, list)

    # Si hay ROIC calculado, debe ser un ratio dentro de rango razonable
    for roic in report.roic_series:
        # H1 T14: rango que SÍ detecta errores (antes −500 %..10 000 %). 5.0 deja pasar el 272 % real de
        # AAPL (IC sin caja ni valores negociables) y rechaza IC degenerados; IC < 5 % de activos ya es n/m.
        assert -1.0 <= roic.value <= 5.0, (
            f"ROIC de {roic.value:.3f} es irrazonable para {archetype_raw['ticker']} "
            f"(período {roic.period})"
        )


# ---------------------------------------------------------------------------
# Test 6: AZO (equity negativo) usa IC operativo — valor calculado a mano (T9)
# ---------------------------------------------------------------------------

def test_azo_invested_capital_uses_operating_method():
    """
    T9 / revisión adversarial A4: equity ≤ 0 → IC operativo SIEMPRE.
    Fixture AZO legacy (sin LiabilitiesCurrent ni ROU): IC = Assets − Cash − STI − LTI − ROU
    = 19,355,324,000 − 271,803,000 − 0 − 0 − 0 = 19,083,521,000 (mismo número que el antiguo
    "total_assets-cash fallback", ahora con método explícito).
    """
    import json
    from pathlib import Path
    from due_diligence.tools.edgar import EdgarAdapter

    raw = json.loads((Path(__file__).parent / "fixtures" / "azo_xbrl_raw.json").read_text())
    report = EdgarAdapter.parse_financials(raw)
    # Fixture regenerado (T14), IC operativo (Koller) por equity ≤ 0: activos 19.355324B − caja 0.271803B
    # − ROU operativo 3.194666B − NIBCL (pasivo corriente 9.519397B − deuda CP 0) = 6.369458B.
    assert report.invested_capital.value == pytest.approx(6_369_458_000)
    assert report.invested_capital.source.startswith("operating")
