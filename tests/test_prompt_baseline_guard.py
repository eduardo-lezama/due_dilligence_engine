"""
Guard de proceso (ADR-038): el prompt del Value Expert no cambia sin pasar la puerta de evals.

Si este test falla has cambiado `_SYSTEM_PROMPT`. Pasos: correr
`scripts/run_evals.py --label cand --compare tests/evals/baselines/full.run.json` (y --with-hypotheses si
la baseline lo usa); si la puerta pasa, guardar el nuevo run como baseline con --save-baseline full.

Deuda registrada (bloque B, ADR-044): `pending_gate.json` guarda la huella de un prompt que Eduardo decidió
cambiar ANTES de pasar la puerta (B4: se re-congela el dataset una sola vez tras B1–B3). El guard acepta
esa huella y solo esa: cualquier cambio posterior vuelve a fallar. Al pasar la puerta y re-guardar las
baselines, `pending_gate.json` se borra (el último test lo exige).
"""
import json
from pathlib import Path

import pytest

from due_diligence.evals.runner import prompt_sha

BASELINES = Path(__file__).parent / "evals" / "baselines"
PENDING = BASELINES / "pending_gate.json"


def _pending_sha() -> str | None:
    return json.loads(PENDING.read_text(encoding="utf-8"))["prompt_sha"] if PENDING.exists() else None


@pytest.mark.parametrize("kind", ["full", "quick"])
def test_expert_prompt_matches_baseline(kind):
    path = BASELINES / f"{kind}.scorecard.json"
    if not path.exists():
        pytest.skip(f"no {kind} baseline yet")
    baseline_sha = json.loads(path.read_text(encoding="utf-8"))["prompt_sha"]
    assert prompt_sha() in (baseline_sha, _pending_sha()), (
        f"Value Expert prompt changed ({baseline_sha} → {prompt_sha()}): run the eval gate and re-save the "
        f"{kind} baseline (see this file's docstring and ADR-038)")


def test_pending_gate_is_removed_once_the_baseline_matches():
    path = BASELINES / "full.scorecard.json"
    if not PENDING.exists() or not path.exists():
        pytest.skip("no pending gate")
    baseline_sha = json.loads(path.read_text(encoding="utf-8"))["prompt_sha"]
    assert _pending_sha() != baseline_sha, "the gate passed: delete tests/evals/baselines/pending_gate.json"
