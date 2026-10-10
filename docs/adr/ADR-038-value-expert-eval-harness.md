# ADR-038: Harness de evaluación del Value Expert y puerta de cambios

**Status**: Accepted (2026-10-10, H2-1)
**Date**: 2026-10-10
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `evals`, `value-expert`, `prompt`, `process`
**Related**: [ADR-005](ADR-005-hallucination-control-by-architecture.md) (cifras sin respaldo), [ADR-030](ADR-030-valuation-convergence-signaling.md) (regla 21), [ADR-032](ADR-032-structured-outputs-fail-loud.md) (salida cruda vs reparada), [ADR-036](ADR-036-evidence-gate.md)

## Context

Hasta H1, cada cambio del prompt o del modelo del Value Expert se aceptaba leyendo uno o dos informes. Con un LLM estocástico eso no separa una mejora del azar. Además, 44 de 51 informes de H1 fueron HOLD, y sin medición no se podía saber si un cambio lo arreglaba o lo empeoraba.

## Decisions

1. **Solo se evalúa la síntesis.** Los casos son estados congelados justo antes del Expert (`scripts/freeze_contexts.py`, `tests/evals/cases/`): 12 tickers en variante base, AAPL con peers, y QLYS con 3 hipótesis inyectadas (`with_hypotheses`). La entrada es idéntica entre versiones. Si cambia el código previo al Expert, se re-congela y se repiten las baselines (`manifest.frozen_with`).
2. **Se mide la obediencia a reglas, no el acierto.** `evals/checks.py` tiene 9 checks deterministas sobre la salida CRUDA del LLM (el código repara las reglas 23 y 24 después y las haría pasar siempre). Cada check aplica o no aplica; "no aplica" no cuenta como acierto.
3. **Se guarda todo.** `evals/runner.py` guarda salida cruda, informe, contexto, coste y los modelos que respondieron. `score()` es pura: cambiar un check y repuntuar no cuesta nada (`--rescore`).
4. **Agregación por caso y puerta pareada.** `evals/scorecard.py` aplica estas reglas:
   - **Mayoría por caso.** Un check vale lo que dice la mayoría de las repeticiones; un empate cuenta como suspenso.
   - **Desacuerdo por pares.** `1 − Σk(k−1)/(n(n−1))`.
   - **Casos que cambian.** Para cada check con al menos 4 casos aplicables, los casos que empeoran menos los que mejoran pueden ser como mucho 1. Con menos de 4 casos aplicables, el check solo se muestra.
   - **Límites de ruido.** Desacuerdo y cifras sin respaldo pueden subir como mucho la referencia más el ruido. Los casos HOLD no pueden subir en 2 o más. Los errores no de transporte pueden subir como mucho en 1.
   - **Runs no comparables.** Si cambian los casos, la forma del run o el dataset, la puerta los rechaza.
5. **Ruido medido, no supuesto.** Hay dos baselines completas del mismo prompt (full-1 y full-2, 14 casos × 5).
   - **Diferencias observadas.** Desacuerdo 0,071 frente a 0,086 (+0,015); cifras sin respaldo 1,63 frente a 1,40 (±0,23); 0 casos que cambian de resultado.
   - **`noise.json`.** Lleva 1 caso, 0,05 de desacuerdo y 0,5 cifras: aproximadamente el doble de lo observado, con suelos, porque solo hay dos muestras.
   - **Comprobación.** full-2 pasa la puerta frente a full.
6. **Regla de proceso.**
   - No se cambia `_SYSTEM_PROMPT`, `_format_context` ni el modelo del Expert sin `scripts/run_evals.py --compare tests/evals/baselines/full.run.json`.
   - Si la puerta pasa, se re-guarda la baseline.
   - `tests/test_prompt_baseline_guard.py` falla si el sha del prompt no coincide con la baseline.
   - Hay una baseline rápida aparte (8 casos × 3, `quick.run.json`) para iterar; no es comparable con la completa.
7. **Seguimiento prospectivo.** La tabla `analyses` de SQLite guarda `price_at_analysis`, `dcf_per_share`, `epv_per_share` y `reverse_dcf_growth`. Es el único camino hacia medir el acierto real (MCE-36) y el antídoto contra optimizar solo la puerta (Goodhart).
8. **Tests metamórficos de la valoración** (`tests/test_valuation_metamorphic.py`, coste 0): sobre los estados reales, más WACC no sube el DCF; el doble de acciones reduce el valor por acción a la mitad; el precio cambia el upside y no el valor intrínseco.

## Calibración de los checks con datos reales

- **`rule21_count` daba falsos positivos en 13 de 14 casos.** El modelo escribe "0 cheap, 1 fair, and 2 expensive out of 4", el formato del propio contexto. El check acepta ahora ese formato. Además, no aplica si ningún método es calculable (PLUG).
- **`ungrounded_numbers`.** La tolerancia depende de la precisión escrita y no se derivan cocientes; así acepta solo el 1–3 % de cifras aleatorias (antes, el 100 %). En la baseline, 3 de 114 cifras marcadas son falsos positivos de signo, aceptados.

- **Segunda calibración (2026-10-10, con gpt-6.1-sol, ADR-040).** Un modelo que razona sobre los veredictos que descarta destapó falsos positivos en los checks de texto, y se corrigieron con frases reales:
  - **Justificación** (`rationale_matches_verdict`): ya no cuentan las cláusulas con negación o condición, como "do not yet establish a definitive SELL case" o "insufficiently resolved for BUY".
  - **Regla 22**: si la frase contrasta el ROIC con otra métrica ("falling ROIC versus improving operating performance"), la palabra de mejora no se atribuye al ROIC.
  - **Cifras**: "16 percent" se trata igual que "16%".

  Lección: **cada modelo nuevo calibra también el instrumento.** Antes de creerse un fallo de la puerta, se lee la salida.

- **Control de gasto (2026-10-10).** Durante un run se agotó el crédito, así que el harness incorpora dos cosas:
  - **Antes de empezar**, `run_evals.py` muestra las llamadas previstas y el coste estimado, a partir del coste por llamada de la baseline.
  - **`--max-cost`** (3 $ por defecto): al superarlo se cancelan las llamadas pendientes; se marcan como `BudgetExceeded` y la puerta da "inconcluso".
  - **Contador del cliente:** ahora aplica las tarifas de caché (entrada cacheada y escritura en caché de las generaciones 5.6/6). Antes sobreestimaba entre un 27 % y 2 veces frente a la factura.

## Alternatives considered

- **Pydantic Evals o DeepEval.** Pydantic Evals añade 8 paquetes nuevos (pydantic-ai-slim y otros) para lo que hacen unas 300 líneas propias. Se reconsidera con el juez LLM (MCE-33).
- **Juez LLM con rúbrica desde el principio.** Un juez sin calibrar contra notas humanas mide su propio sesgo (MCE-33).
- **Tasas globales y "flip rate" (1 − moda/n).** El ruido entre casos taparía los cambios y el flip rate está sesgado con n pequeño. Rechazados.
- **Puntuar el informe reparado.** Las reparaciones del código ocultarían los fallos del modelo. Rechazado.
- **Anonimización, contrafactual y bootstrap.** Aplazados (MCE-35). Con 14 casos, un intervalo bootstrap añade poco a la puerta pareada.

## Consequences

- **Coste.** Un run completo cuesta unos 1,06 $ (gpt-5.4-mini, más el validador de tesis con gpt-4o-mini) y tarda unos 3 minutos. El rápido cuesta unos 0,36 $.
- **Qué no mide.** La puerta mide obediencia a reglas y estabilidad, no si los veredictos aciertan. Para eso sirven MCE-36 y la revisión humana de una muestra.
- **Hallazgos de la primera baseline** (EDGE_CASES, pendientes):
  - HOLD por defecto en 14 de 14 casos.
  - Regla 24 incumplida.
  - El Expert calcula porcentajes propios.
  - BBW es inestable.
- **Ficheros versionados.** Las baselines viven en git (unos 1,9 MB) para que la puerta sea reproducible. Los runs de trabajo viven en `data/evals/runs/` (ignorado).

## References

- Código: `src/due_diligence/evals/` (`dataset.py`, `checks.py`, `runner.py`, `scorecard.py`), `scripts/run_evals.py`, `scripts/freeze_contexts.py`.
- Tests: `tests/test_evals_*.py`, `tests/test_run_evals_cli.py`, `tests/test_prompt_baseline_guard.py`, `tests/test_valuation_metamorphic.py`.
