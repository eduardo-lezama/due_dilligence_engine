"""
Arquetipos MCE-25 de punta a punta en la valoración (T13, ADR-033).

Corre parse_financials → valuation_agent sobre los 4 fixtures (AAPL large cap, BBW small cap,
AZO equity contable negativo, PRDO asset-light / sin capex) con un MarketReport sintético:
un precio redondo fijo por ticker y market cap = precio × acciones de portada.

Invariantes que se comprueban (no valores exactos: los fixtures legacy no traen inputs FCFF):
    - el bridge existe y net_claims = deuda − caja − STI (los fixtures no traen NCI/pref/LTI),
    - el valor por acción es finito,
    - el resultado NO depende del signo del patrimonio contable (AZO: equity negativo),
    - el upside se calcula frente al precio y market EV = precio × acciones + net claims.
"""
import math

import pytest

from due_diligence.agents.valuation import valuation_agent
from due_diligence.schemas.base import MetricValue
from due_diligence.schemas.reports import MarketReport
from due_diligence.tools.edgar import EdgarAdapter

# Precios sintéticos redondos (USD) — solo para tener un precio, no pretenden ser de mercado.
_PRICES = {"AAPL": 250.0, "BBW": 50.0, "AZO": 3_500.0, "PRDO": 30.0}


def _state(fin) -> dict:
    price = _PRICES[fin.ticker.upper()]
    mcap = price * fin.shares_outstanding.value
    return {
        "ticker": fin.ticker,
        "financials": fin,
        "market": MarketReport(
            ticker=fin.ticker, stub=False,
            price_current=MetricValue(value=price, unit="USD", period="2026-10-01", source="synthetic"),
            market_cap=MetricValue(value=mcap, unit="USD", period="2026-10-01", source="synthetic")),
        "checkpoint2_user_input": {"valuation_date": fin.as_of_date.isoformat()},
    }


def test_valuation_archetype_bridge_and_per_share(archetype_raw):
    fin = EdgarAdapter.parse_financials(archetype_raw)
    r = valuation_agent(_state(fin))["valuation"]
    assert r.stub is False and r.error is None
    b = r.equity_bridge
    assert b is not None
    sti = fin.short_term_investments.value if fin.short_term_investments else 0.0
    # LTI y participaciones por puesta en equivalencia también son derechos a favor (ADR-033).
    lti = fin.long_term_investments.value if fin.long_term_investments else 0.0
    emi = fin.equity_method_investments.value if fin.equity_method_investments else 0.0
    mi = fin.minority_interest.value if fin.minority_interest else 0.0
    pref = fin.preferred_stock.value if fin.preferred_stock else 0.0
    assert b.net_claims == pytest.approx(
        fin.total_debt.value + mi + pref - fin.cash_and_equivalents.value - sti - lti - emi)
    assert r.dcf_intrinsic_per_share is not None and math.isfinite(r.dcf_intrinsic_per_share.value)
    # market EV = precio × acciones usadas + net claims
    price = r.price_current.value
    assert r.market_enterprise_value.value == pytest.approx(price * r.share_count_used + b.net_claims, rel=1e-6)
    assert r.dcf_upside_pct == pytest.approx(
        (r.dcf_intrinsic_per_share.value - price) / price * 100, abs=0.1)


def test_valuation_independent_of_book_equity_sign(archetype_raw):
    """El bridge usa derechos, no patrimonio contable: invertir el signo del equity no cambia nada."""
    fin = EdgarAdapter.parse_financials(archetype_raw)
    if fin.stockholders_equity is None:
        pytest.skip("fixture sin stockholders_equity")
    flipped = fin.model_copy(update={"stockholders_equity": MetricValue(
        value=-fin.stockholders_equity.value, unit="USD", period=fin.stockholders_equity.period, source="test")})
    a = valuation_agent(_state(fin))["valuation"]
    b = valuation_agent(_state(flipped))["valuation"]
    assert a.dcf_intrinsic_per_share.value == pytest.approx(b.dcf_intrinsic_per_share.value)
    assert a.epv_per_share.value == pytest.approx(b.epv_per_share.value)
