# ADR-018: Socratic Checkpoints v2 — preguntas ancladas en datos

**Status**: Accepted
**Date**: 2026-04-21
**Extends**: [ADR-006](ADR-006-socratic-hitl.md)

## Context

ADR-006 especificó Socratic HITL en dos checkpoints con "razonamiento didáctico". Phase 1 shipped con una implementación mínima:
- **Checkpoint 1**: `typer.confirm("Proceed?")` — una sola pregunta binaria.
- **Checkpoint 2**: `typer.prompt` por cada asunción de valuation (WACC, growth, years).

Ambas son **approval gates**, no Socratic. La parte "didáctica" nunca se implementó porque requiere que las preguntas estén ancladas en datos reales del análisis — algo que en Phase 1 no estaba priorizado.

El resultado observado en las corridas de AAPL/FSLR:
- Eduardo aprobaba sin fricción cognitiva, perdiendo el valor didáctico del sistema.
- Checkpoint 2 aceptaba WACC=9% y growth=2.5% sin contrastar contra CAGR histórico — asunciones cómodas pero no justificadas.
- La "checkpoint as learning opportunity" quedó solo en el papel.

El objetivo dual del sistema (decidir + aprender) solo se cumple si los checkpoints fuerzan articulación consciente.

## Decision

### Checkpoint 1 (Socratic)

1. **Nueva función `_build_socratic_questions(state) -> list[str]`** en `agents/checkpoint.py`. Genera 1–3 preguntas basadas en patrones detectados en los outputs de especialistas:
   - **Moat vs ROIC mismatch**: si `moat_classification == "narrow"` y `ROIC > 20%` → "Moat etiquetado 'narrow' pero ROIC es X%. ¿Qué señal reconciliaría esto o indicaría moat erosion?"
   - **Insider ownership bajo**: si `< 1%` → "Insider ownership es X%. ¿Misalignment o normal para mega-cap?"
   - **CEO tenure largo**: si `> 12 años` → "CEO lleva N años. ¿Continuity asset o succession risk?"
   - **Peer prompt** (siempre presente, por ADR-015): "¿Contra qué 3–5 peer tickers benchmarkear?"
2. **CLI rendering**: las preguntas se muestran con formato destacado, cada una pide respuesta free-text (opcional — skip permitido con default vacío).
3. **Propagación al Expert**: las respuestas se guardan en `state["checkpoint1_user_input"]["answers"]` y el Value Expert las recibe como contexto adicional en su prompt.

### Checkpoint 2 (historical anchor)

1. Antes de pedir WACC/growth, mostrar:
   - CAGR histórico 5y de revenue (inyectado en state por ADR-019).
   - Mensaje: "Tu growth y WACC propuestos deberían ser defendibles contra este anchor."
2. Pedir una justificación libre: `"Why do your assumptions diverge (or align) with this historical rate?"`
3. La justificación se guarda como `assumptions["growth_justification"]` y llega al Value Expert junto con los números.

### Graceful degradation

- Si un reporte de especialista no está disponible (stub), su pattern-trigger se salta — no se generan preguntas sin datos.
- Respuesta vacía = skip. No se fuerza input.
- Empty list de questions = solo se pregunta peers (siempre se pregunta).

## Consequences

- **+** Cumple el objetivo dual (decidir + aprender) explícitamente.
- **+** Las respuestas enriquecen el contexto del Value Expert con conocimiento tácito del usuario ("sé que AAPL tiene un moat wider que 'narrow' porque X").
- **+** El ancla histórica en Checkpoint 2 combate el anchoring a asunciones default cómodas (9% / 2.5%).
- **+** Habilita la pregunta de peers de ADR-015 como parte integrada del flujo.
- **+** Patrón heurístico — no cuesta LLM calls adicionales.
- **−** Complejidad moderada del CLI (más prompts, más parsing de respuestas).
- **−** El usuario puede skipear todo — el valor didáctico depende de su compromiso. Mitigado por ergonomía: las preguntas son cortas y visibles.
- **−** Las heurísticas de pattern-detection son fijas — no cubren todos los casos. Evolución natural: añadir triggers según feedback real.

## Alternatives considered

- **Preguntas fijas** independientes del state: rechazado — no son Socratic si no están ancladas en los datos específicos del análisis.
- **LLM-generated questions** con un micro-prompt específico para el checkpoint: rechazado para Phase 2 por YAGNI y coste. Se puede evolucionar en Phase 3 si las heurísticas resultan limitantes.
- **Checkpoint 2 sin historical anchor**: rechazado — el gap entre growth propuesto y CAGR histórico es exactamente la decisión de valuation más crítica.
- **Respuestas obligatorias**: rechazado — bloquearía al usuario en corridas rápidas. Skip permitido es el trade-off correcto.

## Relación con otros ADRs

- **Extiende ADR-006**: materializa la parte Socratic que quedó pendiente.
- **Depende de ADR-019**: el CAGR histórico que alimenta Checkpoint 2 se computa en el Financials agent por ADR-019.
- **Habilita ADR-015**: la pregunta de peers forma parte integral del Socratic de Checkpoint 1.
