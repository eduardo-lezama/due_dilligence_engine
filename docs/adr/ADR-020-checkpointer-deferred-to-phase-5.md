# ADR-020: SqliteSaver checkpointer diferido a Phase 5

**Status**: Accepted
**Date**: 2026-04-21
**Extends**: (nueva decisión — documenta lo que `cli.py:386-389` ya implementaba)

## Context

Phase 1 implementó el CLI con `MemorySaver` como checkpointer de LangGraph y dejó documentado en `cli.py:386-389`:

> MemorySaver mantiene el estado en RAM durante el run interactivo.
> Evita completamente el problema de serialización msgpack de Pydantic con
> SqliteSaver. La persistencia real (markdown + SQLite) la hace el archiver_node.
> SqliteSaver se usará en Phase 5 (webapp) donde se necesita reanudar entre sesiones.

El Plan agent de Phase 2 propuso hacer el swap `MemorySaver → SqliteSaver` como Task 2 prioritaria ("cross-session resume, unblocks Phase 5"). Antes de empezar la implementación, la revisión identificó dos problemas:

1. **Bug conocido sin revalidar.** El `AnalysisState` contiene sub-modelos Pydantic v2 (`FilingsReport`, `CompetitiveReport`, `ValueExpertReport`, `FinancialsReport`, etc.). LangGraph serializa el state con msgpack al escribir cada checkpoint. msgpack no maneja objetos Pydantic arbitrarios sin un `default=` custom → excepción en el primer `interrupt()`. Este fue el fallo observado en una sesión previa y por el que se introdujo `MemorySaver`.

2. **La persistencia real ya existe.** `archiver_node` escribe markdown a `data/reports/<TICKER>-<DATE>.md` y metadata a `data/analyses.db` (SQLite propio). Una corrida completa dura < 3 minutos en CLI; no hay caso de uso real de "matar la CLI y reanudar horas después" en modo single-user. Task 2 aportaba `Ctrl+C → resume` pero ese flujo no lo usa Eduardo hoy.

3. **El valor real llega en Phase 5.** La webapp (Phase 5, NAS QNAP TS264 — ADR-010) sí necesita resume entre sesiones de browser, multi-user, y persistencia de thread state. Ahí el checkpointer importa y el coste de resolver bien la serialización (serde custom o `JsonPlusSerializer`) vale la pena.

## Decision

1. **Mantener `MemorySaver` en el CLI de Phase 1/2.** No se hace swap en Phase 2.
2. **SqliteSaver se aborda en Phase 5** como parte del bootstrap de la webapp. Para entonces los modelos Pydantic pueden haber evolucionado y el approach correcto se decide sobre el state final.
3. **Caminos evaluados para Phase 5** (decisión final en Phase 5, no aquí):
   - `serde=JsonPlusSerializer()` nativo de LangGraph — en versiones recientes maneja Pydantic v2 out-of-the-box. Validar.
   - `serde=` custom que convierte sub-modelos Pydantic vía `.model_dump()` al escribir y reconstruye con `ModelClass.model_validate()` al leer.
   - Convertir el state a `dict` puro (Pydantic solo en los nodos de negocio, `dict` en el state del grafo). Más invasivo.
4. **DoD de Phase 2 actualizado**: se elimina el ítem "resume test" (antes §6.2 del strategic review).
5. **`pyproject.toml` mantiene `langgraph-checkpoint-sqlite>=2.0.0`** — la dependencia ya está instalada, pero no se usa desde `cli.py` hasta Phase 5. No se desinstala para evitar churn en el lockfile.

## Consequences

- **+** Evita reintroducir un bug conocido sin necesidad real.
- **+** Ahorra 30-45 min de implementación + tests de round-trip de serialización.
- **+** Deja la decisión de serde (JsonPlusSerializer vs custom vs dict-state) para cuando exista el state definitivo, no sobre el estado actual en evolución.
- **+** El archiver ya cubre el único caso real de persistencia que Eduardo necesita hoy (reporte markdown final).
- **−** El CLI no tolera `Ctrl+C → resume`. Aceptable: corridas < 3 min, no es un caso de uso activo.
- **−** Phase 5 tendrá que pagar la deuda técnica de la serialización. Aceptable: coincide con el momento en que el valor del checkpointer es máximo.

## Alternatives considered

- **Hacer el swap ahora con `JsonPlusSerializer` como primera opción**: rechazado — requiere validar la compat Pydantic v2 de la versión instalada, escribir test de round-trip con todos los sub-modelos, y tener fallback a serde custom si falla. Esfuerzo real ~1-2h sin valor incremental en CLI single-user.
- **Convertir el state a dict puro en Phase 2**: rechazado — refactor invasivo que toca todos los nodos. No YAGNI.
- **Mantener dependencia instalada pero sin usarla + TODO**: aceptado (parte de §Decision).

## Relación con otros ADRs

- **Complementa ADR-007**: el storage SQLite de archivado ya cubre la necesidad de persistencia real del reporte.
- **Precondición para Phase 5 (ADR-010)**: la webapp necesita resolver esto; este ADR deja la decisión abierta para entonces.
- **Afecta Phase 2 strategic review (`docs/phase-2-strategic-review.md`)**: se elimina el ítem 2 de Prioridad 1 y el DoD §6.2.
