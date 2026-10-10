"""
scorecard — puntúa un EvalRun y compara dos runs con una puerta pareada caso a caso (H2-1).

`score` es una función pura sobre el EvalRun guardado: si mañana cambia un check se repuntúa el
mismo run sin volver a pagar al LLM. Se agrega POR CASO (mayoría de las repeticiones) porque las
tasas globales mezclan casos distintos y su ruido supera las diferencias que queremos ver.
`compare` decide si un candidato (otro prompt u otro modelo) puede sustituir a la baseline: tolera
el ruido medido entre dos baselines (GateNoise) y nada más. Ver ADR-038.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from due_diligence.evals.checks import run_checks
from due_diligence.evals.runner import EvalRun
from due_diligence.schemas.llm_outputs import ValueExpertLLMOutput
from due_diligence.schemas.reports import ValueExpertReport

_CONTINUOUS = ("ungrounded_numbers", "repairs")     # se comparan por su media, no por aprobado/suspenso
_MIN_APPLICABLE_CASES = 4                           # con menos casos, un check diagnóstico solo informa
# Reglas de veredicto: una sola regresión bloquea, sin mínimo de cobertura. Son raras en el dataset
# (3 casos divergentes, 1 con hipótesis) y son justo lo que un cambio sicofántico rompería.
_HARD_CHECKS = ("buy_floor", "rule21_divergence_no_buy", "rule24_hypotheses")


class CaseResult(BaseModel):
    """Resumen de un caso sobre sus repeticiones válidas."""
    case: str
    n_ok_runs: int
    verdicts: dict[str, int]
    disagreement: float
    checks: dict[str, Optional[bool]]   # mayoría de las repeticiones donde aplica; None si nunca aplica
    mean_ungrounded: float
    mean_repairs: float


class Scorecard(BaseModel):
    """Puntuación de un run completo, con los metadatos necesarios para saber si dos son comparables."""
    label: str
    cases: list[str]
    repeat: int
    quick: bool
    prompt_sha: str
    expert_model_requested: str
    calls_by_model: dict[str, int]
    manifest_frozen_with: dict[str, str]
    cost_usd: float
    case_results: list[CaseResult]
    hold_cases: int                      # casos cuyo veredicto mayoritario es HOLD
    mean_disagreement: float
    mean_ungrounded: float
    n_errors_non_transport: int
    n_errors_transport: int


class GateNoise(BaseModel):
    """Ruido tolerado entre baseline y candidato (calibrado con dos baselines del mismo prompt)."""
    max_case_flips: int = 1
    disagreement_delta: float
    ungrounded_delta: float


class GateResult(BaseModel):
    passed: bool
    reasons: list[str]
    report_only: list[str] = []          # checks con < 4 casos aplicables: se muestran, no bloquean


def disagreement(verdict_counts: dict[str, int]) -> float:
    """Probabilidad de que dos repeticiones distintas (sin reemplazo) den veredictos distintos.

    1 − Σ k(k−1) / (n(n−1)). A diferencia del "flip rate" (1 − moda/n) no está sesgado con n pequeño.
    """
    n = sum(verdict_counts.values())
    if n < 2:
        return 0.0
    return 1 - sum(k * (k - 1) for k in verdict_counts.values()) / (n * (n - 1))


def _majority(values: list[bool]) -> Optional[bool]:
    """True si aprueba más de la mitad; un empate cuenta como suspenso (conservador). None si vacío."""
    if not values:
        return None
    return sum(values) * 2 > len(values)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def score(run: EvalRun, states: dict, *, label: str) -> Scorecard:
    """Aplica los checks a cada repetición válida y agrega por caso. Sin LLM ni red."""
    results: list[CaseResult] = []
    for case in run.cases:
        ok = [r for r in run.records if r.case == case and r.raw is not None and r.error is None]
        binary: dict[str, list[bool]] = {}
        ungrounded, repairs, verdicts = [], [], Counter()
        for rec in ok:
            raw = ValueExpertLLMOutput(**rec.raw)
            report = ValueExpertReport(**rec.report)
            verdicts[raw.verdict] += 1
            for c in run_checks(raw, report, run.contexts[case], states[case]):
                if c.name == "ungrounded_numbers":
                    ungrounded.append(c.value or 0.0)
                elif c.name == "repairs":
                    repairs.append(c.value or 0.0)
                else:
                    binary.setdefault(c.name, [])
                    if c.applicable and c.passed is not None:
                        binary[c.name].append(c.passed)
        results.append(CaseResult(case=case, n_ok_runs=len(ok), verdicts=dict(verdicts),
                                  disagreement=round(disagreement(verdicts), 4),
                                  checks={name: _majority(v) for name, v in binary.items()},
                                  mean_ungrounded=round(_mean(ungrounded), 3), mean_repairs=round(_mean(repairs), 3)))

    with_runs = [cr for cr in results if cr.n_ok_runs]
    errors = [r for r in run.records if r.error]
    return Scorecard(
        label=label, cases=list(run.cases), repeat=run.repeat, quick=run.quick, prompt_sha=run.prompt_sha,
        expert_model_requested=run.expert_model_requested, calls_by_model=run.calls_by_model,
        manifest_frozen_with=run.manifest_frozen_with, cost_usd=run.cost_usd, case_results=results,
        hold_cases=sum(1 for cr in with_runs if Counter(cr.verdicts).most_common(1)[0][0] == "HOLD"),
        mean_disagreement=round(_mean([cr.disagreement for cr in with_runs]), 4),
        mean_ungrounded=round(_mean([cr.mean_ungrounded for cr in with_runs]), 3),
        n_errors_non_transport=sum(1 for r in errors if not r.transport_error),
        n_errors_transport=sum(1 for r in errors if r.transport_error),
    )


def compare(base: Scorecard, cand: Scorecard, *, noise: GateNoise) -> GateResult:
    """Puerta pareada: el candidato no puede empeorar más allá del ruido medido entre baselines."""
    if sorted(base.cases) != sorted(cand.cases):
        return GateResult(passed=False, reasons=[f"different case set: {sorted(set(base.cases) ^ set(cand.cases))}"])
    if (base.repeat, base.quick) != (cand.repeat, cand.quick):
        return GateResult(passed=False, reasons=[f"different run shape: repeat/quick {base.repeat}/{base.quick} "
                                                 f"vs {cand.repeat}/{cand.quick}"])
    if base.manifest_frozen_with != cand.manifest_frozen_with:
        return GateResult(passed=False, reasons=["different dataset (manifest frozen_with differs): re-run the "
                                                 "baseline on the current dataset"])

    # Un caso sin suficientes repeticiones válidas (p.ej. 429 en todas) desaparecería de la comparación:
    # sin datos no hay aprobado, hay "inconcluso" (revisión final H2-1).
    min_ok = (base.repeat + 1) // 2
    thin = sorted(cr.case for sc in (base, cand) for cr in sc.case_results if cr.n_ok_runs < min_ok)
    if thin:
        return GateResult(passed=False, reasons=[f"inconclusive: fewer than {min_ok} valid runs in {sorted(set(thin))}"])

    reasons: list[str] = []
    report_only: list[str] = []
    if cand.n_errors_non_transport > base.n_errors_non_transport + 1:
        reasons.append(f"non-transport errors {base.n_errors_non_transport} → {cand.n_errors_non_transport}")

    base_by = {cr.case: cr for cr in base.case_results}
    names = sorted({n for cr in base.case_results + cand.case_results for n in cr.checks})
    for name in names:
        pairs = [(base_by[cr.case].checks.get(name), cr.checks.get(name)) for cr in cand.case_results]
        pairs = [(b, c) for b, c in pairs if b is not None and c is not None]
        if name in _HARD_CHECKS:
            worse = [b and not c for b, c in pairs].count(True)
            if worse:
                reasons.append(f"{name}: {worse} case(s) now violate a verdict rule")
            continue
        if len(pairs) < _MIN_APPLICABLE_CASES:
            report_only.append(name)
            continue
        worse = sum(1 for b, c in pairs if b and not c)
        better = sum(1 for b, c in pairs if c and not b)
        if worse - better > noise.max_case_flips:
            reasons.append(f"{name}: {worse} cases regressed, {better} improved (net > {noise.max_case_flips})")

    if cand.mean_disagreement > base.mean_disagreement + noise.disagreement_delta:
        reasons.append(f"verdict disagreement {base.mean_disagreement:.3f} → {cand.mean_disagreement:.3f}")
    if cand.mean_ungrounded > base.mean_ungrounded + noise.ungrounded_delta:
        reasons.append(f"ungrounded numbers/report {base.mean_ungrounded:.2f} → {cand.mean_ungrounded:.2f}")
    if cand.hold_cases - base.hold_cases >= 2:
        reasons.append(f"HOLD cases {base.hold_cases} → {cand.hold_cases} (H1 problem: everything HOLD)")
    return GateResult(passed=not reasons, reasons=reasons, report_only=report_only)


def save_run(run: EvalRun, path: Path) -> None:
    """Guarda el run completo (respuestas crudas incluidas) para poder repuntuarlo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=1), encoding="utf-8")


def load_run(path: Path) -> EvalRun:
    return EvalRun.model_validate_json(Path(path).read_text(encoding="utf-8"))
