# ADR-032 — Structured outputs nativos y fail-loud en la capa LLM

**Status**: Proposed (pasa a Accepted al cerrar H1 Task 2)
**Date**: 2026-10-03
**Triggered by**: auditoría 2026-10-02 (H16, H17, H2), EC-68, EC-69, EC-71
**Relacionados**: ADR-004 (lo amplía), ADR-005 (lo implementa), ADR-009, ADR-021, ADR-031

---

## Context

Antes de H1, todos los agentes LLM extraían JSON con `find("{")`/`rfind("}")` + `json.loads`
y construían los reports campo a campo con `.get()`. Ante cualquier excepción fabricaban un
report "válido" (`stub=False` con `"[Parse error]"` en un campo de texto; el Value Expert
devolvía `verdict="HOLD"` con confianza 0, que se archivaba como `-HOLD.md`).

El cliente (`llm/client.py`) hacía fallback a OpenAI ante **cualquier** `Exception` de
Anthropic, incluidos bugs propios; no contabilizaba el uso de Claude; trataba la truncación
(`finish_reason == "length"`) como un warning; y `LLMError` existía pero nunca se lanzaba.

Además, los modelos Claude actuales (Opus 4.7+, Sonnet 5.5, Fable) rechazan `temperature`
con un 400, y con thinking siempre activo el primer bloque de la respuesta puede no ser texto.

## Decision

1. **`LLMClient.structured(schema, messages, ...)`** es el único camino para salidas
   estructuradas.
   - OpenAI: `chat.completions.parse(response_format=<PydanticModel>)` (json_schema strict).
   - Anthropic: `messages.create(output_config={"format": {"type": "json_schema", ...}})`
     + comprobación de `stop_reason` + `model_validate_json` propio. No se usa
     `messages.parse()` porque valida dentro de su parser (`anthropic/lib/_parse/_response.py:16-20`)
     antes de exponer `stop_reason`, y una truncación aparecería como error de schema.
2. **Errores tipados** en `llm/errors.py` (módulo sin imports de SDK; estable ante
   `importlib.reload` en tests): `LLMError` ← `LLMProviderError(provider, fallback_eligible)`,
   `LLMTruncatedError`, `LLMSchemaError`.
3. **Fallback acotado**: solo `LLMProviderError(fallback_eligible=True)` (conexión,
   401/403/408/409/429/5xx). Nunca ante truncación, schema inválido, refusal o 400/404/422.
4. **Contabilidad honesta**: `_record_usage` es el único punto de registro; incluye Claude;
   `RunStats.calls_by_model` y `LLMClient.last_model_used` dicen qué modelo respondió de verdad.
5. **Compatibilidad de modelos Claude**: `temperature` solo se envía a familias que la aceptan
   (4.5/4.6, Haiku 4.5); el texto se toma del primer bloque `type == "text"`.
6. *(Task 2)* Schemas de salida LLM separados de los reports; agentes con `LLMError` →
   `stub=True, error=...`; sin VERDICT → `INCOMPLETE`.
7. *(Task 3)* Validación de citas dentro del nodo; números del LLM: determinista > cita
   verificada > descarte.

## Consequences

- (+) Los fallos son visibles y tipados; el fallback ya no oculta bugs propios.
- (+) El coste por run incluye Claude y el informe puede decir qué modelo respondió.
- (−) `complete()` se mantiene como camino legacy (con su `except Exception`) hasta que
  todos los agentes migren en Task 2; se elimina en H2.

## Alternatives considered

- `instructor` u otra librería de structured outputs: dependencia nueva sin ganancia sobre los
  helpers nativos de los SDK instalados (YAGNI).
- JSON mode + parser propio: es lo que falló.
- `anthropic.messages.parse`: oculta `stop_reason` (ver Decision 1).
