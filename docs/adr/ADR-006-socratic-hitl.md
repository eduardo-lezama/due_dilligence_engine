# ADR-006: Socratic HITL en 2 checkpoints

**Status**: Accepted
**Date**: 2026-04-19

## Context
El sistema sirve **dos objetivos simultáneamente**: ayudar a decidir inversiones **y formar al usuario** en análisis value. Fire-and-forget puro (input → output 15 min después) no forma al usuario. Chat interactivo continuo fatiga y ralentiza. Checkpoints bien colocados son el sweet spot.

Además, en value investing los supuestos clave (WACC, growth, terminal, moat classification) son donde el humano aporta más valor — dejarle intervenir allí previene el "GIGO" del DCF.

## Decision
El grafo se detiene vía `LangGraph.interrupt()` en **2 puntos**:

### Checkpoint 1 — Post-investigación
Tras los 5 especialistas, antes de Valuation. El sistema presenta:
1. **Hallazgos clave**: moat identificado, insider ownership, compensation structure, red flags, señales de calidad de earnings.
2. **Razonamiento didáctico** (Socratic): explica *por qué* cada señal importa (e.g. "ROIC 22% sostenido 10 años sugiere moat estructural; considera qué podría erosionarlo").
3. **Pregunta socrática** al usuario: p.ej. "¿qué señal te hace pensar que este management es trustworthy, y qué verías como red flag?".
4. Integra la respuesta del usuario al State.

### Checkpoint 2 — Pre-valuation synthesis
Tras Valuation, antes de Value Expert. El sistema presenta:
1. Supuestos propuestos del DCF (WACC, revenue growth, margin, terminal).
2. Sensitivity table (intrinsic value en rango con distintos supuestos).
3. Razonamiento de por qué cada supuesto es razonable.
4. Permite al usuario **ajustar** (overriding valores) o **aceptar**.

## Consequences
- **+** Cumple objetivo dual: decisión + aprendizaje.
- **+** Usuario previene GIGO en el DCF.
- **+** Cada checkpoint queda registrado en el report (trazabilidad: "supuesto X fue editado por usuario el [fecha]").
- **−** UX Fase 1 (CLI) debe soportar pause/resume limpiamente. LangGraph `interrupt()` + SQLite checkpointer nativos lo resuelven.
- **−** Análisis no es 100% fire-and-forget — pero el usuario ha decidido explícitamente que prefiere esto.

## Open question (a resolver en Fase 0)
Formato concreto del output didáctico: plain CLI vs `rich` panels vs YAML structured prompt. Se probarán 2-3 formatos en los primeros análisis y se elige el más usable.

## Alternatives considered
- **Fire-and-forget puro**: rechazado por perder el objetivo didáctico.
- **Chat interactivo continuo**: rechazado por latencia y drift.
- **HITL sin razonamiento didáctico** (solo confirm/reject): rechazado — no aporta al aprendizaje.
