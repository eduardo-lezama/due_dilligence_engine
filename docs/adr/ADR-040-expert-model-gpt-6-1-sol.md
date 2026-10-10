# ADR-040: Modelo del Value Expert y del Critic: gpt-6.1-sol (sustituye a gpt-5.4-mini)

**Status**: Accepted (2026-10-10)
**Date**: 2026-10-10
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `llm`, `models`, `value-expert`, `evals`
**Related**: [ADR-038](ADR-038-value-expert-eval-harness.md) (puerta de evals), [ADR-039](ADR-039-specialist-model-gpt-6-luna.md) (especialistas)

## Context

Es el primer cambio de modelo del Expert que pasa por la puerta de ADR-038. El Expert es el paso de juicio: cruza 9 secciones, reglas y señales contradictorias. Con gpt-5.4-mini daba HOLD en 13 de 14 casos e incluía unas 1,7 cifras sin respaldo por informe, cifras que generaba el propio LLM.

## Evidencia

- **Prueba rápida (8 casos × 3).** gpt-6.1-sol pasó la puerta. gpt-6-luna falló: más desacuerdo (+0,083) y más cifras sin respaldo (3,2 frente a 2,0).
- **Prueba completa (14 casos × 5, mismo prompt `ba6fe2a993e6` y mismo dataset congelado con luna).** gpt-6.1-sol pasó la puerta:

| Métrica | gpt-5.4-mini | gpt-6.1-sol |
|---|---|---|
| Cifras sin respaldo por informe | 1,74 | 0,07 |
| Desacuerdo medio entre repeticiones | 0,086 | 0,043 |
| Casos HOLD (mayoría) | 13/14 | 9/14 (SELL: AAPL, AAPL-peers, AZO, NUE, PLUG) |
| Regla 24 (hipótesis etiquetadas) | falla | cumple |
| Errores | 0 | 0 |

- **Coste real** (panel de OpenAI): unos 0,018 $ por llamada del Expert, frente a unos 0,013 $ con gpt-5.4-mini. Nuestro contador da cifras unas 2 veces más altas porque ignora la caché.
- **Lectura cualitativa.** Resuelve los conflictos de forma explícita ("I adjudicate that conflict in favor of valuation discipline"), separa "buena empresa cara" de "empresa rota", y no hace cálculos propios con las cifras.

## Decisions

1. **Modelo.** `OPENAI_EXPERT_MODEL=gpt-6.1-sol` (por defecto en `config.py` y en `.env.example`).
2. **Baselines.** La completa pasa a ser el run de sol. La rápida se deriva de ese mismo run (8 casos base × 3 primeras repeticiones), sin coste extra.
3. **Techos de tokens** (sol razona; EC-99): Expert 32 768 y Critic 16 384. Son techos, no coste.
4. **Calibración de checks antes de comparar.** sol razona de forma explícita sobre los veredictos que descarta. Eso destapó falsos positivos en los checks de texto, que se corrigieron con TDD y frases reales antes de decidir:
   - `rationale_matches_verdict`: descarta las cláusulas con negación o condición.
   - `rule22`: trocea las frases por palabras de contraste.
   - `ungrounded_numbers`: entiende "percent".

   Toda la comparación se hizo con los checks ya calibrados, repuntuando también la baseline.

## Consecuencias

- **El Critic también cambia a sol**, porque comparte `openai_expert_model`. La puerta no lo evalúa; su efecto se verá en H2-3.
- **HOLD por defecto se reduce pero no desaparece.** Siguen 9 de 14 casos en HOLD, y no hay ningún BUY ni siquiera en BBW, con 3 de 4 métodos en "cheap". La taxonomía de veredicto (H2-2) sigue siendo necesaria y se medirá ya con este modelo.
- **QLYS es el único caso inestable** (3 HOLD y 2 SELL).
- **Latencia.** Un run completo pasó de unos 3 minutos a más de 10.

## Alternatives considered

- **Mantener gpt-5.4-mini:** peor en todas las métricas, por un ahorro de unos 0,005 $ por análisis.
- **gpt-6-luna:** no pasó la puerta.
- **gpt-6-astra o gpt-5.5:** entre 5 y 10 veces más caros. No se probaron, porque sol ya resuelve los problemas que mide la puerta.

## References

- Runs: `data/evals/runs/2026-10-10-full-gpt-6.1-sol-2.run.json` (local) y `tests/evals/baselines/full.run.json`.
- Precios: https://platform.openai.com/docs/pricing (consultado el 2026-10-10).
