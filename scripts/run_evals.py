"""
run_evals — ejecuta el harness del Value Expert, puntúa y (opcional) pasa la puerta contra una baseline.

Es la ÚNICA forma aceptada de validar un cambio del prompt o del modelo del Expert (ADR-038):
    PYTHONUTF8=1 uv run python scripts/run_evals.py --label cand --compare tests/evals/baselines/full.run.json
Otros usos:
    --quick                 8 casos base × 3 repeticiones (iteración barata; baseline propia: quick.run.json)
    --rescore RUN.json      repuntúa un run guardado sin LLM (tras cambiar un check)
    --save-baseline full    guarda el run como baseline (también su scorecard) en tests/evals/baselines/
La baseline se repuntúa con los checks ACTUALES antes de comparar: ambos lados miden lo mismo.
Sale con código 1 si la puerta falla.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table

from due_diligence.evals.dataset import load_manifest, select_cases, states_for
from due_diligence.evals.runner import run_eval
from due_diligence.evals.scorecard import GateNoise, Scorecard, compare, load_run, save_run, score

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "tests" / "evals" / "cases"
BASELINES_DIR = ROOT / "tests" / "evals" / "baselines"
RUNS_DIR = ROOT / "data" / "evals" / "runs"          # gitignored: runs de trabajo
NOISE_FILE = BASELINES_DIR / "noise.json"            # ruido medido entre dos baselines (Task 5)
# Antes de medir el ruido: valores prudentes. noise.json los sustituye.
_DEFAULT_NOISE = GateNoise(max_case_flips=1, disagreement_delta=0.10, ungrounded_delta=0.5)

console = Console()


def load_noise(path: Path = NOISE_FILE) -> GateNoise:
    return GateNoise.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else _DEFAULT_NOISE


def _fmt_checks(checks: dict) -> str:
    failed = [n for n, v in checks.items() if v is False]
    return ", ".join(failed) if failed else "—"


def print_scorecard(sc: Scorecard) -> None:
    """Tabla por caso + resumen. Sin verborrea: lo que falla y cuánto varía."""
    t = Table(title=f"{sc.label} · prompt {sc.prompt_sha} · {sc.expert_model_requested} · ${sc.cost_usd:.2f}")
    for col in ("case", "runs", "verdicts", "disagree", "failed checks", "ungrounded", "repairs"):
        t.add_column(col)
    for cr in sc.case_results:
        verdicts = " ".join(f"{v}×{n}" for v, n in sorted(cr.verdicts.items()))
        t.add_row(cr.case, str(cr.n_ok_runs), verdicts, f"{cr.disagreement:.2f}", _fmt_checks(cr.checks),
                  f"{cr.mean_ungrounded:.1f}", f"{cr.mean_repairs:.1f}")
    console.print(t)
    console.print(f"HOLD cases {sc.hold_cases}/{len(sc.cases)} · mean disagreement {sc.mean_disagreement:.3f} · "
                  f"mean ungrounded {sc.mean_ungrounded:.2f} · errors {sc.n_errors_non_transport} "
                  f"(+{sc.n_errors_transport} transport) · models {sc.calls_by_model}")


def _rescored(run_path: Path, label: str) -> tuple[Scorecard, object]:
    run = load_run(run_path)
    manifest = load_manifest(CASES_DIR / "manifest.json")
    return score(run, states_for(run.cases, manifest, CASES_DIR), label=label), run


def _baseline_cost_per_call() -> float | None:
    """Coste medio por llamada del Expert en la baseline completa (para estimar antes de gastar)."""
    path = BASELINES_DIR / "full.scorecard.json"
    if not path.exists():
        return None
    sc = Scorecard.model_validate_json(path.read_text(encoding="utf-8"))
    calls = len(sc.cases) * sc.repeat
    return sc.cost_usd / calls if calls and sc.cost_usd else None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", default="run")
    p.add_argument("--repeat", type=int, default=5)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--expert-model", default=None)
    p.add_argument("--with-hypotheses", action="store_true")
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--max-cost", type=float, default=3.0,
                   help="tope de gasto en $ (contador con caché); al superarlo el run para y la puerta da inconcluso")
    p.add_argument("--compare", type=Path, default=None)
    p.add_argument("--rescore", type=Path, default=None)
    p.add_argument("--save-baseline", choices=("full", "quick"), default=None)
    args = p.parse_args(argv)

    if args.rescore:
        sc, run = _rescored(args.rescore, args.label)
        run_path = args.rescore
    else:
        manifest = load_manifest(CASES_DIR / "manifest.json")
        repeat = 3 if args.quick else args.repeat
        cases = select_cases(manifest, CASES_DIR, quick=args.quick, hypotheses=args.with_hypotheses)
        n_calls = len(cases) * repeat
        per_call = _baseline_cost_per_call()
        estimate = f"~${n_calls * per_call:.2f}" if per_call else "unknown (no baseline cost)"
        console.print(f"{n_calls} Expert calls · estimated {estimate} (baseline model) · cap ${args.max_cost:.2f}")
        run = run_eval(cases, repeat=repeat, expert_model=args.expert_model, max_workers=args.max_workers,
                       manifest_frozen_with=manifest.frozen_with, quick=args.quick, max_cost_usd=args.max_cost)
        run_path = RUNS_DIR / f"{date.today().isoformat()}-{args.label}.run.json"
        save_run(run, run_path)
        sc = score(run, dict(cases), label=args.label)
        run_path.with_name(run_path.name.replace(".run.json", ".scorecard.json")).write_text(
            sc.model_dump_json(indent=1), encoding="utf-8")
        console.print(f"saved {run_path}")
    print_scorecard(sc)

    if args.save_baseline:
        if (args.save_baseline == "quick") != run.quick:
            console.print("[red]--save-baseline kind does not match the run (quick vs full)[/red]")
            return 1
        BASELINES_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_path, BASELINES_DIR / f"{args.save_baseline}.run.json")
        (BASELINES_DIR / f"{args.save_baseline}.scorecard.json").write_text(sc.model_dump_json(indent=1),
                                                                             encoding="utf-8")
        console.print(f"baseline saved: {args.save_baseline}")

    if args.compare:
        base, _ = _rescored(args.compare, f"baseline:{args.compare.stem}")
        gate = compare(base, sc, noise=load_noise())
        colour = "green" if gate.passed else "red"
        console.print(f"[{colour}]GATE {'PASSED' if gate.passed else 'FAILED'}[/{colour}] vs {args.compare.name}")
        for r in gate.reasons:
            console.print(f"  - {r}")
        if gate.report_only:
            console.print(f"  report-only (< 4 applicable cases): {', '.join(gate.report_only)}")
        return 0 if gate.passed else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
