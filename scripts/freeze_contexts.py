"""
freeze_contexts — congela la línea base de lo que ve el Value Expert (H1 Task 0b).

Por qué existe: el Horizonte 1 cambia el prompt y el contexto del Value Expert
(T2d, T4a, T4b) antes de que exista el harness de evals (H2-1). Si no guardamos
ahora el contexto exacto que recibe el Expert con el código actual, la línea base
para comparar "antes vs después" se pierde para siempre.

Cómo funciona: conduce el grafo real igual que la CLI, pero sin preguntas: resume
los dos interrupt() con defaults y se detiene ANTES de value_expert
(`interrupt_before=["value_expert"]`). Guarda dos archivos por ticker en
tests/evals/contexts/: el texto de `_format_context` (.md) y el AnalysisState
serializado (.state.json). Coste ≈ especialistas LLM por ticker (~0,02 €).

Uso (requiere .env con API keys; PYTHONUTF8=1 en Windows: structlog imprime "≥"):
    PYTHONUTF8=1 uv run python scripts/freeze_contexts.py AAPL BBW AZO PRDO KBR PATH MOS QLYS
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "evals" / "contexts"

# Resume del checkpoint 2 con los defaults históricos de la CLI. Un dict vacío no
# sirve: LangGraph trata Command(resume={}) como falsy y no reanuda (ver cli.py).
_CHECKPOINT2_DEFAULTS = {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10}


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


def freeze_context(ticker: str, out_dir: Path, graph: Any = None, today: str | None = None) -> Path:
    """Corre el grafo hasta justo antes del Value Expert y guarda contexto + estado.

    Args:
        ticker: símbolo US.
        out_dir: carpeta de salida (se crea si no existe).
        graph: grafo compilado inyectable (tests); None → build_graph(make_checkpointer()).
        today: fecha ISO para el nombre de archivo; None → hoy.

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
    config = {"configurable": {"thread_id": f"freeze-{ticker}-{today}"}}

    # Mismo recorrido que cli.analyze_cmd, sin interacción humana.
    graph.invoke({"ticker": ticker}, config=config)
    graph.invoke(Command(resume={"proceed": True, "peers": "", "answers": {}}), config=config)
    graph.invoke(Command(resume=dict(_CHECKPOINT2_DEFAULTS)), config=config,
                 interrupt_before=["value_expert"])

    state = graph.get_state(config).values
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{ticker}-{today}.md"
    md_path.write_text(_format_context(state), encoding="utf-8")
    (out_dir / f"{ticker}-{today}.state.json").write_text(
        json.dumps(_to_jsonable(dict(state)), indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path


def main(argv: list[str]) -> int:
    """CLI mínima: un ticker por argumento; informa de cada archivo escrito."""
    if not argv:
        print(__doc__)
        return 1
    for t in argv:
        path = freeze_context(t, DEFAULT_OUT_DIR)
        print(f"frozen {t.upper()} → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
