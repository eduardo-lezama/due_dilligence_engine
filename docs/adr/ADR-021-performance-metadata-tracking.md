# ADR-021: Performance Metadata Tracking — latencia y coste por análisis

**Status**: Accepted
**Date**: 2026-04-25

## Context

Cada análisis invoca 5-7 llamadas LLM (specialists + Expert + Critic) y entre 10-20 llamadas a APIs externas (EDGAR, Polygon, Finnhub, Tavily). Sin tracking, no sabemos:
- Cuánto tarda un análisis de extremo a extremo.
- Cuánto cuesta en tokens reales.
- Si un ticker concreto es significativamente más caro o lento que la media.

El token usage ya está disponible en `response.usage` de cada respuesta OpenAI — simplemente no lo capturamos. El coste se puede calcular post-hoc con los precios por modelo.

Precios confirmados (USD/1M tokens, abril 2026):
- `gpt-4o-mini`: $0.15 input / $0.60 output (specialists)
- `gpt-5.4-mini`: $0.75 input / $4.50 output (Expert + Critic)
- `gpt-4.1`: $2.00 input / $8.00 output (fallback premium, raro en uso normal)

## Decision

### Arquitectura: acumulador en LLMClient (Opción B)

No se usa LangGraph state reducers (`Annotated[float, operator.add]`) para evitar cambios en la serialización de `AnalysisState` y los msgpack warnings existentes (ADR-020). El acumulador vive en el singleton `LLMClient`:

- `reset_run_stats()` — llamado en CLI antes del primer `graph.invoke()`
- `get_run_stats() → RunStats` — leído en CLI después de que el archiver termina
- `RunStats`: `tokens_in: int`, `tokens_out: int`, `cost_usd: float`, `calls: int`

La latencia total se mide con `time.time()` en el CLI: `t0` antes del primer invoke, `t1` después del archiver.

### Persistencia post-archiver

Como la latencia solo se conoce DESPUÉS de que el archiver ha corrido, la persistencia ocurre en el CLI tras el análisis:

1. **SQLite**: `UPDATE analyses SET latency_ms=?, cost_eur=? WHERE run_id=?`
2. **Markdown**: el CLI appenda un bloque `## Performance` al final del report.

Esto mantiene el archiver sin cambios y concentra el tracking en el CLI.

### Tipos de cambio

```
config.py          → usd_eur_rate (float, default 0.93, env USD_EUR_RATE)
llm/client.py      → RunStats dataclass + reset/get methods + captura usage
cli.py             → time.time() wrap + reset/get stats + update SQLite + append markdown
agents/archiver.py → ADD COLUMN latency_ms + cost_eur a _CREATE_TABLE_SQL
                     (migration para DBs existentes con ALTER TABLE + ignore si ya existe)
```

### No incluido en este ADR (deferred)

- **Breakdown por agente**: cada nodo podría escribir su coste parcial a state. Diferido a Phase 3 — requiere `Annotated` reducers en AnalysisState.
- **Live FX rate**: innecesario para uso personal. Tasa fija en config es suficiente.
- **Dashboard de costes**: Phase 5 webapp.

## Consequences

- **+** Visibilidad inmediata: después de cada análisis se ve cuánto costó y cuánto tardó.
- **+** Histórico en SQLite: consultas como "¿cuánto llevo gastado este mes?" son posibles.
- **+** Zero overhead perceptible: `time.time()` y leer `response.usage` son operaciones de nanosegundos.
- **-** Latencia en markdown/SQLite no incluye el append post-archiver (< 10ms — despreciable).
- **-** Si el proceso muere entre el archiver y el UPDATE de SQLite, la fila queda sin datos de performance. Aceptable para CLI.

## Alternatives considered

- **Opción A — LangGraph reducer**: `Annotated[float, operator.add]` en AnalysisState. Más elegante pero cambia la serialización del state — riesgo de regresar los msgpack warnings (ADR-020). Diferido a Phase 5 con SqliteSaver.
- **Opción C — Solo en archiver**: el archiver recibe los datos como parámetros. Requiere cambiar la interfaz del nodo y añadir lógica no-determinista (timing) dentro del grafo. Viola el principio de que los nodos son funciones puras.
