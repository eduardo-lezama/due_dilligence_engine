# ADR-036: Control de evidencia — un especialista sin citas verificadas es un hueco de datos

**Status**: Accepted (2026-10-09, implemented post-H1, EC-94)
**Date**: 2026-10-09
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `citations`, `evidence`, `fail-loud`, `llm`
**Extends**: [ADR-032](ADR-032-structured-outputs-fail-loud.md) (fail-loud: de "el LLM falló" a "el LLM respondió sin evidencia")
**Related**: [ADR-005](ADR-005-hallucination-control-by-architecture.md) (invariante 2), [ADR-035](ADR-035-document-tables-deterministic.md)

## Context

H1 implementó la validación de citas, pero nadie actuaba sobre su resultado. Medido en los 4 análisis en vivo posteriores a H1: management 0 citas verificadas en 4/4 y filings 0 citas en 3/4. Sin embargo los reports eran `stub=False`: su narrativa llegaba al Value Expert sin etiqueta, el informe omitía la sección en silencio y no se activaba el tope de confianza por falta de datos. Además los apartados del 10-K se recortaban a 8–10K caracteres (15–20 % de cada sección en CALM) y un apartado vacío pasaba sin error.

Una investigación adversarial de los rechazos (AAPL, BBW, CALM) confirmó que el validador NO tenía falsos negativos: todo lo rechazado eran paráfrasis, uniones de dos fragmentos o datos sacados del entrenamiento del modelo (p.ej. "Tim Cook has been CEO since August 24, 2011", fecha ausente de todas las fuentes).

## Decisions

1. **Evidencia = citas del LLM verificadas** (`validation.citations.llm_evidence_count`). Las citas que escribe el código (fila del proxy, EC-93) no cuentan: no son evidencia aportada por el modelo.
2. **Esencial (filings, management) con 0 evidencia = hueco de datos**: entra en DATA GAPS ("0 verified citations — its narrative below is unverified"), activa el tope de confianza 0,6, su bloque en el contexto del Expert empieza por "⚠️ UNVERIFIED" (los datos deterministas del bloque siguen siendo fiables) y el informe dice "### X — no verified evidence".
3. **Reintento único solo en filings** cuando la evidencia es 0: se le muestran los excerpts rechazados y se exige copia literal; se queda la respuesta con más evidencia y, si el reintento falla, la primera. En management no: la causa era estructural (tabla ausente, resuelta en EC-93).
4. **Apartados del 10-K a 25 000 caracteres** (`TENK_ITEM_MAX_CHARS`); los tres vacíos → report con error sin llamar al LLM (fail loud); uno vacío → log.
5. **Antigüedad del CEO**: si ninguna fuente proporcionada contiene el año de nombramiento, null — nunca del conocimiento del modelo.
6. **Las citas no verificadas se listan una vez** por (agente, excerpt) en el informe.

## Alternatives considered

- **`minItems: 1` en el esquema** (soportado por OpenAI strict y Anthropic): convierte "lista vacía" en "al menos una cita posiblemente basura". No mide lo que importa (verificación). Rechazado.
- **Bajar el umbral fuzzy (90)**: los rechazos reales puntuaban 55–84 porque eran paráfrasis o invenciones; un umbral que dejara pasar las paráfrasis dejaría pasar la fecha inventada (55). Rechazado.
- **Reintentar también management**: el problema era de entrada (tabla ausente), no de comportamiento. Rechazado; resuelto en ADR-035.
- **Quitar "Financial data" del corpus citable de competitive** (propuesta del investigador): son datos XBRL deterministas y ADR-024 exige citarlos como evidencia del moat. Rechazado; el rechazo de líneas unidas ya lo hace el validador.

## Consequences

- Un análisis con especialistas sin evidencia ya no puede alcanzar confianza alta ni parecer normal.
- Coste: +1 llamada de gpt-4o-mini solo cuando filings no verifica nada; ~19K tokens de 10-K por análisis.
- **Pendiente operativo**: los especialistas corren con gpt-4o-mini porque `.env` sustituye el default de `config.py` (gpt-4.1). La elección de modelo se mide en H2 con el harness de evals (H2-6).

## References

- Código: `validation/citations.py` (`llm_evidence_count`), `agents/value_expert.py` (`_unevidenced_essentials`, `_UNVERIFIED_NARRATIVE`), `agents/archiver.py` (`_render_citations_section`), `agents/filings.py` (`_retry_for_evidence`), `tools/edgar.py` (`TENK_ITEM_MAX_CHARS`). Tests: `tests/test_evidence_gate.py`.
