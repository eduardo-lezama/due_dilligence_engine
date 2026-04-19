# ADR-008: Vector RAG diferido a Fase 2

**Status**: Accepted
**Date**: 2026-04-19

## Context
El usuario sugirió vector store para que el Value Expert recupere análisis pasados de empresas comparables (mismo sector, mismo moat pattern) como contexto adicional. Es un buen patrón, pero en cold-start el corpus es < 5 análisis — el RAG aporta poco y añade complejidad (embeddings, indexing, similarity, deduplicación).

## Decision
1. **Fase 1**: archivado sin indexing semántico (ver ADR-007). Metadata estructurada en SQLite es suficiente para navegación manual.
2. **Fase 2 activa cuando** el usuario tenga **≥10 análisis archivados** (threshold ajustable si calidad lo justifica antes).
3. **Fase 2 stack**:
   - **ChromaDB local** (single-file, sin servidor).
   - Embeddings `text-embedding-3-small` de OpenAI (barato: $0.02/M tokens).
   - Tool `retrieve_similar_analyses(company_profile) → list[AnalysisSummary]` expuesto al Value Expert.
   - Criterios de similitud: sector, moat_type, size, financial_profile (no solo embedding texto).
4. **Ablation test** en Fase 2: producir un análisis con y sin RAG, comparar cualitativamente la síntesis.

## Consequences
- **+** Evitamos construir componente con bajo ROI en Fase 1.
- **+** Cuando se active, es sobre corpus real.
- **+** `text-embedding-3-small` es barato y bueno para este caso.
- **−** Usuario ve Fase 1 sin RAG y debe esperar.

## Alternatives considered
- **RAG desde Fase 1**: rechazado por cold-start.
- **RAG con corpus sintético para bootstrap**: rechazado — embeddings de análisis sintéticos degradan retrieval real.
- **Usar LanceDB / Qdrant en lugar de ChromaDB**: evaluar en Fase 2; ChromaDB es el default por simplicidad (single-file, zero deps de servidor).
