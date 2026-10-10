"""
runner — ejecuta el Value Expert sobre casos congelados × repeticiones (H2-1, única pieza con LLM).

Por cada repetición guarda la salida CRUDA del LLM y el informe final completos: si mañana cambia un
check, se repuntúa sin volver a pagar (scorecard.score es una función pura sobre el EvalRun). El
contexto que vio el modelo se guarda una vez por caso. Coste y modelos que respondieron salen de
RunStats (el modelo pedido puede no ser el que respondió: fallback entre proveedores, ADR-032).

Limitaciones asumidas: un modelo de Expert por run (se parchea `value_expert.settings`, que el Critic
comparte — relevante en H2-3); el validador de ruptura de tesis usa su propio modelo y no se parchea.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from typing import Optional
from unittest import mock

from pydantic import BaseModel

from due_diligence.agents import value_expert
from due_diligence.agents.value_expert import _format_context, value_expert_agent
from due_diligence.graph.state import AnalysisState
from due_diligence.llm.client import get_client
from due_diligence.schemas.llm_outputs import ValueExpertLLMOutput


class RunRecord(BaseModel):
    """Una repetición de un caso."""
    case: str
    repeat: int
    raw: Optional[dict] = None            # ValueExpertLLMOutput.model_dump(); None si el LLM falló
    report: dict                          # ValueExpertReport.model_dump() (tras reparaciones del código)
    context_sha: str
    error: Optional[str] = None
    transport_error: bool = False         # LLMProviderError (429, red): no cuenta contra la versión
    latency_s: float


class EvalRun(BaseModel):
    """Resultado completo de un run: todo lo necesario para puntuar y repuntuar sin LLM."""
    records: list[RunRecord]
    cases: list[str]
    repeat: int
    contexts: dict[str, str]              # caso → texto de contexto que vio el modelo
    expert_model_requested: str
    calls_by_model: dict[str, int]        # modelos que RESPONDIERON (incluye el validador de tesis)
    cost_usd: float
    prompt_sha: str                       # sha256 del _SYSTEM_PROMPT del Value Expert (12 hex)
    manifest_frozen_with: dict[str, str] = {}
    quick: bool = False


_captured = threading.local()


class _CapturingClient:
    """Envuelve al cliente LLM para quedarse con la salida cruda del Expert en ESTE hilo."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def structured(self, schema, *args, **kwargs):
        out = self._inner.structured(schema, *args, **kwargs)
        if schema is ValueExpertLLMOutput:
            _captured.raw = out          # el reintento por LLMSchemaError sobrescribe: vale el último
        return out

    def __getattr__(self, name):
        return getattr(self._inner, name)


def prompt_sha() -> str:
    """Huella del prompt del Value Expert: la baseline solo es comparable con el mismo prompt."""
    return hashlib.sha256(value_expert._SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


def _run_one(case: str, state: AnalysisState, repeat: int, context_sha: str) -> RunRecord:
    """Una llamada al Expert sobre una COPIA del estado (el agente no debe ver efectos de otras)."""
    _captured.raw = None
    started = time.perf_counter()
    try:
        report = value_expert_agent(copy.deepcopy(state))["synthesis"]
        error = report.error
        report_dump = report.model_dump()
    except Exception as e:   # un bug no debe tumbar un run de pago: se registra como fallo del caso
        error, report_dump = f"Crash: {type(e).__name__}: {e}", {}
    raw = _captured.raw
    return RunRecord(case=case, repeat=repeat, raw=raw.model_dump() if raw is not None else None,
                     report=report_dump, context_sha=context_sha, error=error,
                     transport_error=bool(error and error.startswith("LLMProviderError")),
                     latency_s=round(time.perf_counter() - started, 3))


def _budget_record(case: str, repeat: int, context_sha: str, cap: float) -> RunRecord:
    """Repetición no lanzada por el tope de gasto: no es culpa del modelo (cuenta como transporte) y deja
    el caso con menos repeticiones válidas → la puerta da "inconcluso", nunca un aprobado."""
    return RunRecord(case=case, repeat=repeat, raw=None, report={}, context_sha=context_sha,
                     error=f"BudgetExceeded: run stopped at the ${cap:.2f} cap", transport_error=True, latency_s=0.0)


def run_eval(cases_with_states: list[tuple[str, AnalysisState]], *, repeat: int,
             expert_model: Optional[str], max_workers: int = 4,
             manifest_frozen_with: Optional[dict[str, str]] = None, quick: bool = False,
             max_cost_usd: Optional[float] = None) -> EvalRun:
    """Ejecuta cada caso `repeat` veces en paralelo (hilos: es I/O de red) y devuelve el EvalRun.

    `max_cost_usd`: tope de gasto. Al superarlo se cancelan las llamadas aún no lanzadas (las que ya están
    en vuelo terminan). El coste es el del contador (con caché, aproximado a la factura).
    """
    contexts = {name: _format_context(state) for name, state in cases_with_states}
    shas = {name: hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:12] for name, ctx in contexts.items()}
    stats_client = get_client()
    stats_client.reset_run_stats()
    original_get_client = value_expert.get_client

    with ExitStack() as stack:
        # Parches ANTES de crear el pool y deshechos tras el join: ningún hilo los ve a medias.
        stack.enter_context(mock.patch.object(value_expert, "get_client",
                                              lambda: _CapturingClient(original_get_client())))
        if expert_model:
            stack.enter_context(mock.patch.object(value_expert.settings, "openai_expert_model", expert_model))
        jobs = [(name, state, i) for name, state in cases_with_states for i in range(repeat)]
        def job(name: str, state: AnalysisState, i: int) -> RunRecord:
            # Cada repetición mira el gasto ANTES de llamar al LLM: determinista (no depende de cuándo
            # un hilo recoge la tarea) y nunca empieza una llamada con el tope ya superado.
            if max_cost_usd is not None and stats_client.get_run_stats().cost_usd > max_cost_usd:
                return _budget_record(name, i, shas[name], max_cost_usd)
            return _run_one(name, state, i, shas[name])

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            records = list(pool.map(lambda j: job(*j), jobs))

    stats = stats_client.get_run_stats()
    return EvalRun(records=records, cases=[n for n, _ in cases_with_states], repeat=repeat, contexts=contexts,
                   expert_model_requested=expert_model or value_expert.settings.openai_expert_model,
                   calls_by_model=dict(stats.calls_by_model), cost_usd=round(stats.cost_usd, 6),
                   prompt_sha=prompt_sha(), manifest_frozen_with=manifest_frozen_with or {}, quick=quick)
