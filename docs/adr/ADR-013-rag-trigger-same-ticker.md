# ADR-013: RAG trigger redefinido a re-análisis del mismo ticker

**Status**: Accepted
**Date**: 2026-04-21
**Supersedes**: [ADR-008](ADR-008-vector-rag-deferred.md)

## Context

ADR-008 difería RAG hasta tener ≥10 análisis archivados, asumiendo que el caso dominante sería retrieval de análisis cross-ticker comparables (mismo sector, mismo patrón de moat). Dos sesiones de operación real de Phase 1 revelaron un problema con ese supuesto:

- La similaridad cross-ticker está dominada por ruido sectorial (mismo SIC ≠ mismo negocio — ver ADR-015).
- El workflow real del usuario es **re-análisis trimestral/semestral de ~15 nombres en cartera**, no descubrimiento de peers nuevos.
- La señal de mayor valor es el **delta temporal sobre el mismo ticker** ("¿qué cambió en AAPL desde el análisis anterior?"), no la comparación con otra empresa.

El trigger original (≥10 análisis totales) retrasaba RAG más de la cuenta y, cuando se activase, optimizaba para el caso equivocado.

## Decision

1. **Supersede ADR-008** con este ADR.
2. **Nuevo trigger**: RAG se activa cuando exista **≥3 análisis del mismo ticker** (time-series delta).
3. **Corpus shape**: embedding no del reporte entero sino de un "analysis fingerprint" por análisis:
   - párrafo VERDICT
   - moat classification + sources
   - DCF assumptions (WACC, terminal growth, implied growth)
   - critic weaknesses
   El espacio de similaridad se convierte en "firma del análisis", no prosa libre. Ruido mucho menor.
4. **Stack** (sin cambios respecto a ADR-008): ChromaDB local single-file + embeddings `text-embedding-3-small` de OpenAI.
5. **Tool expuesto al Value Expert**: `retrieve_previous_analyses(ticker) → list[AnalysisFingerprint]` con los N más recientes.
6. **Ablation test en Phase 2.5**: producir un análisis de AAPL (con ≥3 históricos) con y sin RAG, comparar si la síntesis menciona concretamente el delta temporal.
7. **Scope**: Phase 2.5 o Phase 3. **No en Phase 2**.

## Consequences

- **+** Señal-a-ruido mucho mayor en retrieval: "qué cambió" es más accionable que "qué se parece".
- **+** Hace "delta desde el último análisis" un output de primera clase del sistema.
- **+** Alineado con el workflow real (gestión de cartera), no con una fantasía de screener.
- **−** ChromaDB sigue necesario, solo que aterriza más tarde.
- **−** El caso "comparación cross-ticker de peers" ya no lo cubre RAG — queda a cargo de peer lists user-supplied (ADR-015).
- **−** Un trigger por ticker implica que empresas nuevas en la cartera tardan 3 trimestres en beneficiarse de RAG.

## Alternatives considered

- **Mantener el trigger original (≥10 totales)**: rechazado. Optimiza para el caso menos valioso y retrasa más de la cuenta.
- **Trigger híbrido (≥10 totales Y ≥3 mismo ticker)**: rechazado. Añade complejidad sin señal adicional — el trigger mismo-ticker ya implica un corpus mínimo útil.
- **RAG desde Phase 1 con corpus sintético**: rechazado (igual que en ADR-008) — embeddings sintéticos degradan retrieval real.
- **Servicio externo tipo Weaviate Cloud o Pinecone**: rechazado por YAGNI y por coste en self-hosting.

## Relación con otros ADRs

- **Supersede ADR-008**: cambia el trigger y la shape del corpus.
- **Complementa ADR-015**: el caso peer cross-ticker pasa a user input; RAG queda para el caso temporal mismo-ticker.
- **Respeta ADR-005**: los fingerprints se construyen con datos ya validados; zero nueva superficie de alucinación.
