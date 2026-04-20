# Architecture Decision Records

Decisiones arquitectónicas del sistema, numeradas e inmutables. Cambiar una decisión requiere un ADR nuevo que **supersede** al anterior, no editar el original.

## Formato
Cada ADR sigue: Status · Context · Decision · Consequences · Alternatives.

## Estados posibles
- **Proposed**: en discusión
- **Accepted**: vigente
- **Superseded by ADR-NNN**: reemplazada
- **Deprecated**: ya no aplica pero no reemplazada

## Índice

| # | Título | Status | Fase afectada |
|---|---|---|---|
| [001](ADR-001-analyst-first.md) | Analyst-first, screener deferred | Accepted | 1–4 |
| [002](ADR-002-markets-order.md) | Mercados por orden US → CAD → EU | Accepted | 1, 3 |
| [003](ADR-003-supervisor-parallel-specialists.md) | Supervisor + especialistas paralelos (Opción B) | Accepted | 1+ |
| [004](ADR-004-provider-abstraction.md) | Provider abstraction Claude primary + OpenAI fallback | Accepted | 0+ |
| [005](ADR-005-hallucination-control-by-architecture.md) | Control de alucinaciones por arquitectura, no por crítica | Accepted | 0+ |
| [006](ADR-006-socratic-hitl.md) | Socratic HITL en 2 checkpoints | Accepted | 1 |
| [007](ADR-007-sqlite-markdown-storage.md) | SQLite + markdown para storage Fase 1 | Accepted | 1 |
| [008](ADR-008-vector-rag-deferred.md) | Vector RAG diferido a Fase 2 | Accepted | 1, 2 |
| [009](ADR-009-verdict-plus-analysis.md) | Value Expert: análisis estructurado + veredicto | Accepted | 1 |
| [010](ADR-010-nas-deploy-target.md) | Deploy target NAS QNAP TS264 | Accepted | 5 |
| [011](ADR-011-us-data-sources.md) | Fuentes de datos US Fase 1 | Accepted | 1, 3 |
| [012](ADR-012-management-agent-data-sources.md) | Fuentes de datos Management Agent (EDGAR trifecta + Tavily) | Accepted | 1b+ |

## Cómo añadir un ADR
1. Crear `ADR-NNN-slug.md` incrementando N.
2. Usar el formato estándar (ver ADRs existentes).
3. Añadir fila a la tabla arriba.
4. Si supersede otro, actualizar el status del anterior a "Superseded by ADR-NNN".
