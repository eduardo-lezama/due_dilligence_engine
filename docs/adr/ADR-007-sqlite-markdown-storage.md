# ADR-007: SQLite + markdown para storage Fase 1

**Status**: Accepted
**Date**: 2026-04-19

## Context
El usuario quiere archivar cada análisis para consulta futura, posible re-análisis y eventualmente RAG (diferido, ver ADR-008). Opciones: filesystem plano, SQLite, Postgres, MongoDB, base de datos documental, vector store directo.

Volumen real esperado: **decenas a bajos centenares** de análisis al año. Single-user. Backup a NAS trivial.

## Decision
Cada análisis genera **tres artefactos**:

1. **Markdown file** en `./reports/{TICKER}/{YYYY-MM-DD}.md` con análisis completo + citas inline.
2. **Row en SQLite** (`./data/analyses.db`) con metadatos estructurados:
   ```sql
   CREATE TABLE analyses (
       run_id TEXT PRIMARY KEY,
       ticker TEXT NOT NULL,
       cik TEXT,
       as_of_date DATE NOT NULL,
       analysis_date TIMESTAMP NOT NULL,
       verdict TEXT CHECK(verdict IN ('BUY','WATCHLIST','HOLD','AVOID')),
       intrinsic_value_low REAL,
       intrinsic_value_high REAL,
       mos_pct REAL,
       moat_type TEXT,
       key_ratios_json TEXT,    -- Pydantic-serialized
       user_inputs_json TEXT,   -- Checkpoint inputs
       cost_usd REAL,
       confidence TEXT CHECK(confidence IN ('high','medium','low')),
       report_path TEXT NOT NULL
   );
   ```
3. **Raw source cache** en `./cache/{CIK}/{filing_id}/` (JSON + HTML de APIs y filings) para reproducibilidad.

Serialización: Pydantic v2 `model_dump_json()`.

## Consequences
- **+** Backup trivial vía rsync al NAS (`rsync reports/ data/ cache/`).
- **+** Queries SQL simples para navegar histórico por verdict, moat, sector, fecha.
- **+** Sin migraciones ceremonia — si schema evoluciona, SQLite lo absorbe con `ALTER TABLE` o dump+reload.
- **+** Zero runtime dependency (SQLite builtin en Python).
- **−** Sin concurrencia multi-proceso real (irrelevante para single-user).
- **−** No queryable por contenido semántico (eso es Fase 2, ver ADR-008).

## Alternatives considered
- **Solo markdown**: rechazado — queryar por verdict/fecha/métrica se vuelve grep-frankenstein.
- **Postgres**: rechazado por overkill absoluto (containers, users, backups).
- **Vector store directo** (Chroma/LanceDB): diferido hasta tener ≥10 análisis (ADR-008).
- **JSON files + índice en directorio**: rechazado por fragilidad.
