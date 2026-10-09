# ADR-037: Deserialización estricta del checkpointer con allowlist calculada

**Status**: Accepted (2026-10-10, implemented post-H1, EC-95)
**Date**: 2026-10-10
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `langgraph`, `checkpointer`, `security`, `serialization`
**Related**: [ADR-020](ADR-020-checkpointer-deferred-to-phase-5.md) (MemorySaver en CLI, SqliteSaver diferido a la fase 5)

## Context

Al reanudar tras cada `interrupt()` (checkpoints 1 y 2), LangGraph reconstruye `AnalysisState` desde msgpack. En LangGraph 1.1.8 (`langgraph-checkpoint` 4.0.2) los tipos no registrados se reconstruyen con un aviso ("Deserializing unregistered type … will be blocked in a future version"), visible en cada análisis. Verificado en `checkpoint/serde/jsonplus.py` (`_create_msgpack_ext_hook`): cuando se bloquee, un tipo bloqueado **no lanza**, vuelve como datos en bruto. Un `ValuationReport` llegaría al Value Expert como dict: fallo silencioso y lejos de su causa (contrario al invariante 5).

El motivo del bloqueo es seguridad: quien pueda escribir en la base de checkpoints podría provocar ejecución de código al deserializar tipos arbitrarios.

## Decisions

1. **Fábrica única** `graph/checkpointer.py`: `make_checkpointer()` → `MemorySaver(serde=make_serializer())`; `make_serializer()` → `JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_ALLOWLIST)`. CLI, `scripts/e2e_run.py` y `scripts/freeze_contexts.py` la usan; LangGraph no se importa fuera de `graph/` (regla de `graph/CLAUDE.md`).
2. **Allowlist calculada, no escrita a mano**: todos los `BaseModel` definidos en `schemas.base`, `schemas.reports` y `schemas.llm_outputs`. Un schema nuevo entra solo; una lista manual sería un olvido esperando a ocurrir.
3. **Estricto desde ya**: con allowlist explícita, cualquier otro tipo se bloquea hoy (con aviso), no en una versión futura. Mejor descubrirlo ahora en tests que el día de un upgrade.
4. **Tests**: un estado realista (reports, MetricValue, Citation, bridge, ciclicidad, snapshot trimestral, peers) hace ida y vuelta en modo estricto y vuelve como los mismos objetos y sin avisos; guard de cobertura de la allowlist; un tipo ajeno queda bloqueado; los puntos de entrada usan la fábrica.

## Alternatives considered

- **`LANGGRAPH_STRICT_MSGPACK=true` sin allowlist**: bloquearía también nuestros schemas → el estado volvería como dicts. Rechazado.
- **Allowlist manual de (módulo, clase)**: frágil ante schemas nuevos. Rechazado.
- **Silenciar el aviso** (logging): esconde el problema hasta que rompa. Rechazado.

## Consequences

- Desaparece el ruido de avisos en cada análisis y el riesgo de un upgrade silencioso.
- **Fase 5**: el `SqliteSaver` debe construirse con `serde=make_serializer()` (anotado en `docs/mce-evolution.md`).

## References

- `.venv/.../langgraph/checkpoint/serde/jsonplus.py` (`JsonPlusSerializer.__init__`, `_create_msgpack_ext_hook`); LangGraph reference: `SerdeConfig.allowed_msgpack_modules` (Context7).
- Código: `graph/checkpointer.py`. Tests: `tests/test_checkpointer.py`.
