"""
Tests metamórficos de la valoración (H2-1 Task 6) sobre los casos congelados del harness.

No sabemos el valor "correcto" de una empresa, pero sí cómo DEBE cambiar al tocar una entrada:
más WACC no puede subir el DCF, el doble de acciones parte el valor por acción a la mitad y el
precio no cambia el valor intrínseco (solo el upside). Se comprueban sobre estados reales (los 13
casos), que cubren equity negativo, caja neta, pérdidas y cíclicas. Coste 0: sin LLM ni red.
"""
from pathlib import Path

import pytest

from due_diligence.agents.valuation import valuation_agent
from due_diligence.evals.dataset import load_case_state, load_manifest

CASES_DIR = Path(__file__).parent / "evals" / "cases"
_MANIFEST = load_manifest(CASES_DIR / "manifest.json")


def _state(case):
    return load_case_state(case, CASES_DIR)


def _ps(state):
    v = valuation_agent(state)["valuation"]
    return v, (v.dcf_intrinsic_per_share.value if v.dcf_intrinsic_per_share else None)


@pytest.fixture(params=_MANIFEST.cases, ids=lambda c: c.name)
def case_state(request):
    st = _state(request.param)
    base, ps = _ps(st)
    if ps is None:
        pytest.skip(f"{request.param.name}: no DCF per share (loss-making or data gap)")
    return st, base, ps


def test_higher_wacc_never_raises_dcf(case_state):
    st, _, ps = case_state
    cp2 = dict(st["checkpoint2_user_input"])
    cp2["wacc_pct"] = cp2["wacc_pct"] + 1.0
    _, ps_hi = _ps({**st, "checkpoint2_user_input": cp2})
    assert ps_hi <= ps + 1e-9


def test_double_shares_halves_per_share(case_state):
    st, _, ps = case_state
    fin = st["financials"]
    if fin.shares_outstanding is None:
        pytest.skip("share count not from shares_outstanding")
    so = fin.shares_outstanding
    fin2 = fin.model_copy(update={"shares_outstanding": so.model_copy(update={"value": so.value * 2})})
    _, ps2 = _ps({**st, "financials": fin2})
    assert ps2 == pytest.approx(ps / 2, abs=0.011)          # per-share redondeado a 2 decimales


def test_price_changes_upside_not_intrinsic_value(case_state):
    st, base, ps = case_state
    mk = st["market"]
    if mk.price_current is None or ps <= 0 or "market_cap / price" in (base.share_count_source or ""):
        pytest.skip("no price, non-positive value or share count derived from price")
    p = mk.price_current.value
    mk2 = mk.model_copy(update={"price_current": mk.price_current.model_copy(update={"value": p * 1.2})})
    v2, ps2 = _ps({**st, "market": mk2})
    assert ps2 == pytest.approx(ps, abs=1e-9)
    assert v2.dcf_upside_pct == pytest.approx((ps / (1.2 * p) - 1) * 100, abs=0.051)
