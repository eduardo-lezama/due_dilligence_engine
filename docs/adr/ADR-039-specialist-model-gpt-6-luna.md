# ADR-039: Modelo de los especialistas: gpt-6-luna (sustituye a gpt-4o-mini)

**Status**: Accepted (2026-10-10, H2-6 adelantado)
**Date**: 2026-10-10
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `llm`, `models`, `cost`, `evidence`
**Related**: [ADR-004](ADR-004-provider-abstraction.md), [ADR-036](ADR-036-evidence-gate.md) (control de evidencia), [ADR-038](ADR-038-value-expert-eval-harness.md) (regla de vigencia del dataset)

## Context

Los especialistas (management, filings y competitive) usaban gpt-4o-mini, de una generación antigua. En el dataset de H2-1, filings terminaba con 0 citas verificadas en 6 de 13 casos. El control de evidencia (ADR-036) trata esos casos como huecos de datos, y el Expert razonaba con menos información.

## Evidencia

Se volvieron a congelar los 12 tickers base con cada modelo candidato (`data/evals/specialists/`). El coste por análisis se midió con KBR leyendo el `RunStats` real.

| Modelo | Precio oficial ($/M, entrada/salida) | Citas verificadas (mgmt/filings/competitive) | Casos de filings sin evidencia | Coste/análisis |
|---|---|---|---|---|
| gpt-4o-mini | 0,15 / 0,60 | 33 / 18 / 45 = 96 | 6 de 12 | 0,0045 $ |
| **gpt-6-luna** | **0,10 / 0,50** | 103 / 99 / 77 = **279** | 1 de 12 (truncado, EC-99) | **0,0057 $** |
| gpt-5.4-mini | 0,75 / 4,50 | 105 / 108 / 74 = 287 | 0 de 12 | 0,0288 $ |

Tasa de verificación: luna 98 %, gpt-5.4-mini 93–100 %, gpt-4o-mini 87–95 %. La diferencia de verdad está en el **volumen**: gpt-4o-mini emitía pocas citas.

## Decisions

1. **Especialistas con gpt-6-luna.**
   - **Frente a gpt-4o-mini:** 3 veces más evidencia verificada por +0,001 $ por análisis.
   - **Frente a gpt-5.4-mini:** calidad casi igual (279 citas frente a 287) a 1/5 del coste.
   - **Cómo se configura:** `OPENAI_FALLBACK_MODEL` (por defecto en `config.py` y en `.env.example`). Se elige el modelo más barato que alcanza la calidad del mejor, en vez del mejor a cualquier precio.
2. **Cambios para modelos de razonamiento (EC-99).**
   - **`temperature`:** no se envía a `gpt-6*` ni `gpt-5.6*`.
   - **Techo de 16 384 tokens:** en las llamadas de los especialistas, porque los tokens de razonamiento cuentan dentro del límite.
3. **Lo que no cambia.**
   - **Expert y Critic:** siguen con gpt-5.4-mini. Cambiarlos exige la puerta de ADR-038.
   - **Validador de tesis:** sigue con gpt-4o-mini. Es una clasificación binaria rara con `max_tokens=200`; a un modelo de razonamiento no le bastaría ese techo.
4. **Vigencia del dataset (ADR-038).**
   - **Dataset:** se volvieron a congelar los 13 casos con luna. Los especialistas tienen evidencia en los 13 y no hay errores.
   - **Baselines:** se regeneraron la completa y la rápida.
   - **`noise.json`:** se mantiene, porque mide la variabilidad del Expert.

## Consecuencias

- **Coste.** Los especialistas cuestan unos 0,006 $ por análisis.
- **Efecto en el Expert (baseline nueva).**
  - **PLUG:** pasa de HOLD a SELL en 4 de 5 repeticiones.
  - **HOLD:** sigue en 13 de 14 casos.
  - **Cifras sin respaldo:** suben de 1,6 a 1,9 por informe. No es comparable 1:1, porque el dataset es otro y el contexto tiene más cifras citadas.
- **Latencia.** luna razona (effort por defecto `medium`), así que es más lento que gpt-4o-mini.
- **Medición de calidad.** No hay una puerta automática para los especialistas; esta comparación es puntual. Una métrica de citas verificadas por especialista entraría en el harness si se repite este tipo de cambio (MCE-34).

## Alternatives considered

- **gpt-5.4-mini.** Es la mejor calidad por un margen muy pequeño, pero cuesta 5 veces más.
- **gpt-5.6-luna.** Es más caro que gpt-6-luna y de una generación anterior; no se midió.
- **Mantener gpt-4o-mini.** La falta de evidencia en filings era el peor eslabón del sistema.

## References

- Precios: https://platform.openai.com/docs/pricing (consultado el 2026-10-10). Modelo: https://platform.openai.com/docs/models/gpt-6-luna.
- Código: `llm/client.py` (`_openai_accepts_temperature`, `_MODEL_PRICING`), `config.py`, `agents/{management,filings,competitive}.py`.
