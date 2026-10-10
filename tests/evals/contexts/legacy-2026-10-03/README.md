# Línea base congelada del Value Expert (H1 Task 0b)

Contexto exacto (`_format_context`) y `AnalysisState` previo al Value Expert, capturados
el 2026-10-03 con el código **anterior** a los cambios de prompt y contexto de H1
(commit `2020973`). Sirven como línea base "antes" para el harness de evals de H2-1.

- `{TICKER}-{fecha}.md`: texto que recibe el Value Expert como user prompt.
- `{TICKER}-{fecha}.state.json`: estado del grafo serializado (reports Pydantic con `model_dump`).
- Defaults usados: sin peers, sin respuestas socráticas, WACC 9 %, g 2,5 %, 10 años.

Regenerar (requiere `.env` con claves):

    PYTHONUTF8=1 uv run python scripts/freeze_contexts.py AAPL BBW AZO PRDO KBR PATH MOS QLYS

No editar a mano: son datos de referencia.

## Estado desde H2-1 (2026-10-10): solo referencia histórica

Estos estados cargan en los schemas actuales pero **no representan el sistema actual** y no se
usan en el harness de evals:
- sus citas no tienen `verified` → con el control de evidencia (EC-94) todas las réplicas caerían en
  DATA GAPS con la confianza topada a 0,6;
- la valoración es la anterior a FCFF + bridge (ADR-033): p.ej. AAPL DCF $73,80/acción;
- no cubren peers, hipótesis del inversor, datos trimestrales ni gobierno.

El dataset vigente está en `tests/evals/cases/` (congelado con el código actual, ver su `manifest.json`).
Estos ficheros sirven para comparar "antes de H1" frente a "después".
