"""
freeze_contexts — congela lo que ve el Value Expert para el harness de evals (H1 T0b, H2-1 Task 1).

Por qué existe: para medir si un cambio de prompt o modelo mejora la síntesis, la entrada del
Expert debe ser idéntica entre versiones. Este script conduce el grafo real igual que la CLI,
sin preguntas: reanuda el checkpoint 1 (con peers en la variante "peers") y el checkpoint 2 con
los defaults que propone el propio nodo, y se detiene ANTES de value_expert
(`interrupt_before=["value_expert"]`). Guarda por caso el texto de `_format_context` (.md), el
AnalysisState serializado (.state.json) y su entrada en manifest.json (con git sha, fecha y
modelo de especialistas: si cambia el código previo al Expert, se re-congela).

Uso (requiere .env con API keys; PYTHONUTF8=1 en Windows):
    PYTHONUTF8=1 uv run python scripts/freeze_contexts.py AAPL BBW AZO PRDO CALM KBR PATH MOS QLYS NUE NKE PLUG
    PYTHONUTF8=1 uv run python scripts/freeze_contexts.py --variant peers AAPL
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from due_diligence.llm.client import get_client

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "evals" / "cases"

# Fallback del checkpoint 2 si el nodo no expone defaults (grafos de test). Un dict vacío no
# sirve: LangGraph trata Command(resume={}) como falsy y no reanuda (ver cli.py).
_CHECKPOINT2_FALLBACK = {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10}

# Peers explícitos de la variante "peers" (regla 18 del Value Expert: valoración relativa).
_PEERS = {"AAPL": "MSFT,GOOGL"}

# Qué ejercita cada ticker (cobertura del dataset; tests/test_evals_dataset.py la exige).
_TAGS = {
    "AAPL": ["archetype"], "BBW": ["archetype"], "AZO": ["archetype"], "PRDO": ["archetype"],
    "CALM": ["cyclical", "archetype"], "NUE": ["cyclical"], "MOS": ["cyclical"],
    "PATH": ["multi_class"], "NKE": ["multi_class"], "PLUG": ["loss_making"],
    "QLYS": ["stability_qlys"], "KBR": [],
}


def _to_jsonable(value: Any) -> Any:
    """Serializa recursivamente modelos Pydantic, dicts y listas a JSON nativo."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (date,)):
        return value.isoformat()
    return value


def _checkpoint2_defaults(graph: Any, config: dict) -> dict:
    """Defaults numéricos/booleanos que propone el checkpoint 2 (WACC sugerido real).

    El payload incluye textos como "auto (historical FCF CAGR or 7%)" que ValuationInput no
    acepta (romperían el grafo): solo se pasan valores numéricos o booleanos.
    """
    for task in getattr(graph.get_state(config), "tasks", ()) or ():
        for itr in getattr(task, "interrupts", ()) or ():
            defaults = (itr.value or {}).get("defaults") or {}
            numeric = {k: v for k, v in defaults.items() if isinstance(v, (int, float, bool))}
            if numeric:
                return numeric
    return dict(_CHECKPOINT2_FALLBACK)


def _git_sha() -> str:
    """Commit corto del código con el que se congela; "-dirty" si hay cambios sin commit.

    Sin el sufijo, un congelado hecho con cambios locales quedaría atribuido a un commit que no
    contiene ese código (visto en H2-1: 603520d sin EC-97).
    """
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha


def _update_manifest(out_dir: Path, entry: dict, today: str) -> None:
    """Añade o reemplaza (por nombre) la entrada del caso y refresca `frozen_with`.

    `specialist_model` = modelos que RESPONDIERON en todos los casos (no el configurado: un
    `claude-*` sin clave de Anthropic cae al fallback de OpenAI).
    """
    path = out_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"cases": []}
    manifest["cases"] = [c for c in manifest["cases"] if c["name"] != entry["name"]] + [entry]
    models = sorted({m for c in manifest["cases"] for m in c.get("models", [])})
    manifest["frozen_with"] = {"git_sha": _git_sha(), "date": today,
                               "specialist_model": ",".join(models) or "unknown"}
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def freeze_context(ticker: str, out_dir: Path, *, variant: str = "base", graph: Any = None,
                   today: str | None = None) -> Path:
    """Corre el grafo hasta justo antes del Value Expert y guarda contexto, estado y manifiesto.

    Args:
        ticker: símbolo US.
        out_dir: carpeta de casos (se crea si no existe).
        variant: "base" o "peers" (checkpoint 1 con peers explícitos).
        graph: grafo compilado inyectable (tests); None → build_graph(make_checkpointer()).
        today: fecha ISO; fija también la fecha de valoración (reproducible al re-valorar).

    Returns:
        Ruta del .md con el contexto congelado.
    """
    from langgraph.types import Command

    from due_diligence.agents.value_expert import _format_context

    ticker = ticker.upper()
    today = today or date.today().isoformat()
    if graph is None:
        from due_diligence.graph.checkpointer import make_checkpointer
        from due_diligence.graph.graph import build_graph

        graph = build_graph(checkpointer=make_checkpointer())
    # thread único por ejecución: dos variantes del mismo ticker el mismo día no comparten estado.
    config = {"configurable": {"thread_id": f"freeze-{ticker}-{variant}-{uuid4().hex[:8]}"}}

    peers = _PEERS.get(ticker, "") if variant == "peers" else ""
    client = get_client()
    client.reset_run_stats()          # para saber qué modelos respondieron en ESTE caso
    graph.invoke({"ticker": ticker}, config=config)
    graph.invoke(Command(resume={"proceed": True, "peers": peers, "answers": {}}), config=config)
    cp2 = {**_checkpoint2_defaults(graph, config), "valuation_date": today}
    graph.invoke(Command(resume=cp2), config=config, interrupt_before=["value_expert"])

    state = graph.get_state(config).values
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{ticker}-{variant}"
    md_path = out_dir / f"{name}.md"
    md_path.write_text(_format_context(state), encoding="utf-8")
    (out_dir / f"{name}.state.json").write_text(
        json.dumps(_to_jsonable(dict(state)), indent=2, ensure_ascii=False), encoding="utf-8")

    tags = list(_TAGS.get(ticker, []))
    valuation = state.get("valuation")
    if valuation is None or not getattr(valuation, "valuation_basis", None):
        tags.append("valuation_failed")
    models = sorted(client.get_run_stats().calls_by_model)
    _update_manifest(out_dir, {"name": name, "ticker": ticker, "variant": variant, "tags": tags,
                               "state_file": f"{name}.state.json", "models": models}, today)
    return md_path


def main(argv: list[str]) -> int:
    """CLI mínima: `[--variant base|peers] TICKER...`; un ticker que falla no detiene el resto."""
    variant = "base"
    if argv[:1] == ["--variant"]:
        variant, argv = argv[1], argv[2:]
    if not argv:
        print(__doc__)
        return 1
    failed = []
    for t in argv:
        try:
            path = freeze_context(t, DEFAULT_OUT_DIR, variant=variant)
            print(f"frozen {t.upper()} ({variant}) -> {path}")
        except Exception as e:  # datos externos: anotar y seguir (el plan sustituye el ticker)
            failed.append(t)
            print(f"FAILED {t.upper()} ({variant}): {type(e).__name__}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
