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
