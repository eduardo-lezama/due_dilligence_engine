# ADR-031 — LLM-in-the-Loop Validation para Thesis-Break Self-Check

**Status**: Accepted
**Date**: 2026-05-22
**Triggered by**: EC-59 (PATH false positive), EC-45 (CGNX precedente)

---

## Context

El método `_thesis_break_self_check` en `value_expert.py` fue añadido como defense-in-depth
(EC-45) para detectar casos donde el thesis-breaker definido en §9 ya se cumple en los datos
actuales. La implementación original usaba Python substring matching (`w in text`).

**Problema**: Python `in` es substring, no word-boundary. `"fall" in "falls below 10%"` → True.
Esto causó falsos positivos (EC-59):

- Thesis-breaker PATH §9: *"thesis breaks if FY2027 quarterly revenue **falls** below 10%"*
- Análisis §7 del mismo LLM: *"revenue growth **falling** toward double digits"*
- Python matching: `"fall" in "falls"` → True, `"fall" in "falling"` → True → warning disparado
- Resultado: PATH marcado con `⚠️ THESIS-BREAK SELF-CHECK WARNING` cuando los ingresos crecían +13% YoY

El mismo mecanismo causó el false positive CGNX: `"compress" in "compressing"` → True.

---

## Decision

**Pipeline de dos etapas** para `_thesis_break_self_check`:

### Stage 1 — Python word-boundary pre-filter (fast, zero cost)

Reemplazar `w in text` por `re.search(rf'\b{re.escape(w)}\b', text)`.

Resultado: `"fall"` no matchea `"falls"`, `"falling"`, `"fallen"`. La forma en infinitivo/raíz
en el thesis-breaker ("if revenue fall below X") no se confunde con formas progresivas/futuras
en el análisis de riesgos.

### Stage 2 — LLM semantic validator (cheap model, solo en trigger)

Cuando el Python pre-filter dispara, invocar un modelo cheap (`gpt-4o-mini`,
`settings.openai_validation_model`) con un prompt estrecho que pregunta:

> *"Is the thesis-breaker condition CURRENTLY TRUE based on these excerpts, or only a
> forward-looking risk?"*

Respuesta JSON: `{"is_currently_true": bool, "reason": "..."}`.

Solo si el LLM confirma `true` se añade el `⚠️ WARNING` al `decision_frame`.

**Default conservador**: si el LLM falla (error de red, parsing), retorna `False` → no se añade
el warning. Los falsos negativos son preferibles a los falsos positivos en este contexto.

---

## Alternatives Considered

| Opción | Resultado |
|--------|-----------|
| Pure regex / expanded patterns | Rechazado: NL tiene variantes infinitas; la distinción semántica "condición futura" vs "hecho actual" está fuera del alcance de regex |
| Reforzar Rule 15 en system prompt | Rechazado: Rule 15 ya existía (EC-45) y falló para PATH. Añadir más texto a un prompt ya largo tiene rendimientos decrecientes para una tarea narrow post-hoc |
| Eliminar `_thesis_break_self_check` | Rechazado: el check tiene valor real cuando la tesis SÍ está rota (MRCY: thesis-breaker "if margin declines" + §4 ya mostraba margen descendiendo) |
| Usar `openai_expert_model` (mismo que Value Expert) | Rechazado: la tarea es clasificación binaria de 100 palabras, no requiere reasoning complejo. gpt-4o-mini es suficiente y más barato |

---

## Consequences

**Positivo**:
- Elimina false positives por inflexiones de palabras (PATH, CGNX).
- La validación semántica es robusta ante cualquier phrasing futuro en §9.
- El path feliz (no trigger) es idéntico a antes en rendimiento y coste.

**Negativo**:
- Añade ~1 LLM call (~200 tokens, ~$0.0001) cuando el Python pre-filter dispara.
  Este evento es raro (solo cuando hay overlap semántico real entre §9 y §4/§5/§7).
- Introduce `openai_validation_model` como segundo modelo en el pipeline de value_expert.
  Acotado: solo para esta validación narrow, no para generación de contenido.

**Patrón reusable**:
Este ADR documenta el **"judge LLM" pattern**: un modelo cheap valida el output de un
modelo caro en una tarea narrow binaria. El patrón es aplicable a otros checkpoints
post-hoc en este sistema (e.g., validar coherencia numérica entre §4 y §6).

---

## Related

- EC-45: CGNX — origen de `_thesis_break_self_check`
- EC-59: PATH — false positive que motivó este ADR
- ADR-005: hallucination control — la validación permanece en LLM-lane (no genera números)
- ADR-004: provider abstraction — `_validate_thesis_break_with_llm` usa `get_client()`
