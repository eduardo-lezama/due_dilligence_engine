# ADR-003: Supervisor + especialistas paralelos (Opción B)

**Status**: Accepted
**Date**: 2026-04-19

## Context
Para un análisis fundamental riguroso se necesitan cubrir 9 dimensiones (business, moat, management, financials, growth, valuation, risks, psych, decision). Un único agente LLM secuencial sufre:
- Prompt gigante propenso a drift.
- Sin paralelismo → latencia alta.
- Confusión entre responsabilidades (lane quantitativo vs cualitativo).

Un sistema multi-agente con supervisor + especialistas en paralelo separa responsabilidades, mapea naturalmente al checklist canónico y encaja con la experiencia previa del usuario en LangGraph (labs 5, 7, 8).

## Decision
Arquitectura **Supervisor + 5 especialistas paralelos + Valuation + Value Expert + Critic**, orquestada con **LangGraph**:

1. **Supervisor**: valida ticker, decide routing, asigna budget.
2. **5 especialistas en `Send()` paralelo**: Financials, Management & Skin, Filings RAG, Market & News, Competitive & Moat. Cada uno decide internamente tool-lane vs LLM-lane.
3. **Checkpoint 1 (Socratic HITL)** vía `interrupt()`: moat + management review.
4. **Valuation Agent**: DCF + reverse DCF + multiples + Magic Formula, todo tool-based.
5. **Checkpoint 2 (Socratic HITL)**: supuestos de valuation.
6. **Value Expert**: síntesis con análisis estructurado + veredicto (modelo top).
7. **Critic Agent**: pre-mortem (modelo distinto al Expert, diversidad cognitiva).
8. **Archiver**: persiste markdown + SQLite row + raw cache.

## Consequences
- **+** Separación limpia, mapea 1-a-1 al checklist canónico.
- **+** Paralelismo real → latencia reducida.
- **+** Reutiliza patterns ya aprendidos (supervisor Lab 5, control plane Lab 7, lane separation Lab 8).
- **−** State schema crece (una key por especialista) → requiere diseño Pydantic cuidadoso desde día 0.
- **−** Diversidad cognitiva Expert↔Critic requiere gestión multi-modelo (mitigado por ADR-004).

## Alternatives considered
- **Opción A — Pipeline lineal**: rechazada (sin paralelismo, prompt gigante).
- **Opción C — Control plane completo con authority/policy formales + MCP + RAG desde día 1**: rechazada como over-engineering para MVP personal. Algunos elementos se absorben en fases posteriores.
