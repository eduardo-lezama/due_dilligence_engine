# ADR-041: Taxonomía de veredicto BUY / WATCHLIST / PASS / SELL con decisión numérica en código

**Status**: Accepted (2026-10-11, H2-2) — **parcialmente sustituido por [ADR-044](ADR-044-decision-by-expected-return-vs-hurdle.md)** (2026-10-17): el margen por tipo de negocio (30/50/60 %) y el ancla mín(DCF, EPV) se sustituyen por la rentabilidad frente a un listón. Siguen vigentes las etiquetas, el reparto LLM/código, el "solo más prudente" y la confianza compuesta.
**Date**: 2026-10-11
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `value-expert`, `verdict`, `margin-of-safety`, `evals`
**Related**: [ADR-009](ADR-009-verdict-plus-analysis.md) (VERDICT obligatorio), [ADR-031](ADR-031-llm-in-loop-validation.md) (ruptura de tesis), [ADR-038](ADR-038-value-expert-eval-harness.md) (puerta), [ADR-040](ADR-040-expert-model-gpt-6-1-sol.md)

## Context

En H1, 44 de 51 informes salieron HOLD, y la confianza (0,61–0,84) y el tamaño de posición los generaba el LLM, sin relación con la valoración. Con BUY/HOLD/SELL, "HOLD" mezclaba tres situaciones distintas: buena empresa cara, barata pero sin confirmar y negocio roto. Tampoco decía a qué precio comprar.

## Decisions

Las decisiones de dominio las tomó Eduardo el 2026-10-11.

1. **Etiquetas.**
   - **BUY:** el negocio merece tenerse y el precio está en o por debajo del precio de entrada.
   - **WATCHLIST:** el negocio merece tenerse pero el precio está por encima de la entrada. Siempre lleva su precio de entrada.
   - **PASS:** no invertible a un precio razonable.
   - **SELL:** tesis rota, trampa de valor o sobrevaloración severa con señales rojas ("vender si la tienes; evitar si no").
   - HOLD solo existe en informes H1.
2. **El LLM elige la etiqueta y el tipo de negocio; el código pone los números.** El modelo devuelve `verdict`, `business_class` (`quality_compounder` / `average` / `cyclical_or_low_quality`) y `confidence_adjustment` (±0,1). `agents/verdict_policy.py`, que es puro y no usa LLM, calcula el resto:
   - **Margen exigido** (§9.1 del checklist): 30 % compounder de calidad, 50 % negocio normal, 60 % cíclico o de baja calidad.
   - **Ancla de valor:**
     - **Compounders de calidad:** el DCF, siempre que sea fiable (valor terminal ≤ 75 % y crecimiento que no salga del 7 % por defecto).
     - **Resto de casos, y cuando el DCF no es fiable:** mín(DCF, EPV).
     - **Sin valores positivos:** no hay ancla, y por tanto no hay precio de entrada.
   - **Precio de entrada** = ancla × (1 − margen exigido).
   - **Confianza:**
     - **Base 0,5.**
     - **Acuerdo de los métodos con la dirección del veredicto:** ±0,05 por método.
     - **DCF no fiable:** −0,1.
     - **Evidencia (≥ 10 citas verificadas):** +0,05.
     - **Ajuste del LLM:** como mucho ±0,1.
     - **Topes ya existentes:** 0,6 por huecos de datos o por governance.
   - **Tamaño de posición:**
     - **Cualquier veredicto que no sea BUY:** 0.
     - **BUY:** 5 % de base; +2,5 % si el margen real es al menos 1,5 veces el exigido; −2,5 % si el precio supera el EPV.
     - **Máximo:** 10 % (§9.2).
3. **El código solo hace el veredicto más prudente.**
   - **Tipo de negocio:**
     - Una empresa cíclica siempre se clasifica como `cyclical_or_low_quality`.
     - Sin moat confirmado no puede ser compounder.
   - **BUY sin margen o sin precio de entrada:** pasa a WATCHLIST. El veredicto original queda en `verdict_raw`.
   - El código nunca sube un veredicto.
4. **Ruptura de tesis confirmada:** resta 0,1 de confianza y no fuerza SELL (ADR-031: el detector tiene falsos positivos).
5. **Contexto del Expert:** el bloque `## DECISION INPUTS` muestra el precio de entrada de cada tipo de negocio *permitido*. Mostrar el de un tipo vetado invitaría a forzarlo.
6. **Sin regla de distancia para WATCHLIST.** Se descartó rebajar a PASS cuando el precio supera la entrada en más de 1,5×: era ingeniería de etiquetas. WATCHLIST con su precio de entrada ya es información accionable; el inversor decide qué vigilar.
7. **Historia sin tocar:** los informes y filas H1 no se reetiquetan. `methodology_version` (H1/H2) los distingue y SQLite guarda las columnas nuevas.

## Evidencia (puerta de ADR-038, 14 casos × 5, gpt-6.1-sol)

La puerta pasó frente a la baseline H1 de sol.

| Métrica | H1 (BUY/HOLD/SELL) | H2 |
|---|---|---|
| Reparto (mayoría por caso) | 9 HOLD, 5 SELL | 9 WATCHLIST, 4 SELL, 1 PASS |
| Cifras sin respaldo por informe | 0,07 | 0,19 |
| BUY sin margen (`buy_mos_floor`) | — | 0 |
| Errores | 0 | 0 |

- **Ningún BUY.** En este dataset casi todo cotiza por encima de su precio de entrada. Puede ser la respuesta correcta, o un sesgo de la valoración; se investiga en el diagnóstico de valoración.
- **Desacuerdo de 0,18.** No es comparable con el de 3 etiquetas, y `noise.json` se midió con H1. Es una limitación conocida.

## Consecuencias

- **`evals/legacy.py`** permite repuntuar los runs H1. La puerta compara entre taxonomías con las métricas de veredicto solo informativas, y los checks duros (incluido el nuevo `buy_mos_floor`) cuentan una violación nueva aunque la baseline no aplicara.
- **Pesos de la confianza sin calibrar:** se calibrarán con el seguimiento prospectivo (MCE-36).
- **El margen y la entrada heredan la calidad del DCF y del EPV.** La siguiente prioridad es validar la valoración, no afinar etiquetas.

## Alternatives considered

- **Mantener BUY/HOLD/SELL añadiendo solo el precio de entrada.** Seguía mezclando situaciones distintas bajo HOLD.
- **Ancla mín(DCF, EPV) siempre.** Haría casi imposible comprar un compounder: no paga crecimiento demostrado.
- **Forzar SELL con la ruptura de tesis.** El detector tiene falsos positivos.
- **Regla de distancia para WATCHLIST.** Sobreingeniería (ver decisión 6).
