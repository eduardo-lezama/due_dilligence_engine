"""
checkpointer — fábrica única del checkpointer de LangGraph con deserialización estricta (EC-95, ADR-037).

Por qué existe: al reanudar tras un interrupt() LangGraph reconstruye el estado desde msgpack.
En 1.1.8 los tipos no registrados se reconstruyen con un aviso; en una versión futura se
BLOQUEARÁN, y un tipo bloqueado no lanza: vuelve como datos en bruto (jsonplus.py,
_create_msgpack_ext_hook). Un ValuationReport llegaría al Value Expert como dict, lejos de la
causa. Aquí se registra explícitamente cada modelo Pydantic de `due_diligence.schemas`
(calculado, no escrito a mano: un schema nuevo entra solo) y lo demás queda bloqueado YA.
"""
from __future__ import annotations

import inspect
from types import ModuleType

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from due_diligence.schemas import base, llm_outputs, reports

# Módulos cuyos modelos pueden viajar en AnalysisState (graph/state.py). Si se crea un módulo
# de schemas nuevo, añadirlo aquí; tests/test_checkpointer.py comprueba la cobertura.
_SCHEMA_MODULES: tuple[ModuleType, ...] = (base, reports, llm_outputs)


def _schema_models(modules: tuple[ModuleType, ...]) -> tuple[tuple[str, str], ...]:
    """(módulo, clase) de cada BaseModel DEFINIDO en esos módulos (no los importados)."""
    return tuple(sorted(
        (module.__name__, name)
        for module in modules
        for name, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == module.__name__
    ))


CHECKPOINT_ALLOWLIST: tuple[tuple[str, str], ...] = _schema_models(_SCHEMA_MODULES)


def make_serializer() -> JsonPlusSerializer:
    """Serializador estricto: solo los tipos seguros de LangGraph + nuestros schemas."""
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_ALLOWLIST)


def make_checkpointer() -> MemorySaver:
    """Checkpointer en memoria de la CLI y los scripts (ADR-020: SqliteSaver diferido a la fase 5;
    cuando llegue, debe construirse con `serde=make_serializer()` — ver docs/mce-evolution.md)."""
    return MemorySaver(serde=make_serializer())
