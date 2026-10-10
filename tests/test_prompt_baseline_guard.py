"""
Guard de proceso (ADR-038): el prompt del Value Expert no cambia sin pasar la puerta de evals.

Si este test falla has cambiado `_SYSTEM_PROMPT`. Pasos: correr
`scripts/run_evals.py --label cand --compare tests/evals/baselines/full.run.json` (y --with-hypotheses si
la baseline lo usa); si la puerta pasa, guardar el nuevo run como baseline con --save-baseline full.
"""
import json
from pathlib import Path

import pytest

from due_diligence.evals.runner import prompt_sha

BASELINES = Path(__file__).parent / "evals" / "baselines"


@pytest.mark.parametrize("kind", ["full", "quick"])
def test_expert_prompt_matches_baseline(kind):
    path = BASELINES / f"{kind}.scorecard.json"
    if not path.exists():
        pytest.skip(f"no {kind} baseline yet")
    baseline_sha = json.loads(path.read_text(encoding="utf-8"))["prompt_sha"]
    assert prompt_sha() == baseline_sha, (
        f"Value Expert prompt changed ({baseline_sha} → {prompt_sha()}): run the eval gate and re-save the "
        f"{kind} baseline (see this file's docstring and ADR-038)")
