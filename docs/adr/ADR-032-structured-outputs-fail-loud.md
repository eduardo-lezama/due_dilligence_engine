# ADR-032 — Structured outputs nativos y fail-loud en la capa LLM

**Status**: Accepted (H1 Task 2e, 2026-10-03)
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
6. *(Task 2)* **Contrato LLM separado del contrato de dominio y fallo visible.**
   - `schemas/llm_outputs.py`: un schema por agente (`FilingsLLMOutput`, `ManagementLLMOutput`,
     `CompetitiveLLMOutput`, `CriticLLMOutput`, `ValueExpertLLMOutput`, `ThesisBreakCheck`),
     todo *required*, `X | None`, `extra="forbid"`, sin restricciones numéricas (los clamps van
     en código). `CitationOut.to_citation()` produce `Citation(verified=False)`.
   - Los reports LLM heredan de `LLMAgentReport(ticker, stub, error, model_used)`.
     `stub=True + error` = el agente corrió y falló; checkpoints antiguos cargan con `error=None`.
   - Patrón único por agente: `structured()` → `_build_report(out, ...)` (mapeo puro) →
     `model_used` desde `client.last_model_used`; `except LLMError` → `stub=True, error="<Clase>: msg"`
     (`agents/llm_support.py`). `form4_transactions_12m` deja de pedirse al LLM.
   - Value Expert: reintento ×1 **solo** ante `LLMSchemaError`; truncación/proveedor → síntesis
     `stub=True` **sin VERDICT** (nunca un HOLD de relleno). Validador ADR-031 devuelve
     `bool | None`; `None` añade el caveat "validator unavailable".
   - Critic no llama al LLM si la síntesis no existe o es stub (`error="skipped: synthesis unavailable"`).
   - Visibilidad: `archiver.report_filename` → `-INCOMPLETE.md` sin VERDICT (antes `PENDING`);
     `collect_failures`/`_render_failures` → sección "## Analysis incomplete"; CLI
     `_show_failures` (panel rojo en checkpoint 1 y antes del VERDICT); bloque `## DATA GAPS`
     al principio del contexto del Expert y tope determinista `verdict_confidence ≤ 0.6` si
     falta Filings o Management; "Models used" en el pie del report y en el bloque Performance.
   - SQLite: columnas `status`, `models_used`, `n_verified_citations`, `methodology_version="H1"`
     (migración idempotente `ALTER TABLE ... ADD COLUMN`; filas antiguas `NULL` = pre-H1).
7. *(Task 3)* **Validación de citas dentro del nodo; números del LLM: determinista > cita
   verificada > descarte.**
   - `validation/citations.py`: substring normalizado o `rapidfuzz.partial_ratio ≥ 90`, y todos
     los números del excerpt en la ventana alineada del corpus; excerpt < 20 caracteres o corpus
     vacío → rechazo. Rechazo = `verified=False`, `confidence=0`, `verification_note`.
   - El corpus es local al nodo (el texto exacto enviado al LLM, por fuente) y se descarta: no
     entra en el estado checkpointeado. `citations`/`unverified_claims`/`cost_usd` de
     `graph/state.py` siguen sin uso (pueden retirarse aparte, MCE-30).
   - Números: `form4_transactions_12m = len(transacciones)` (determinista; sale del schema LLM);
     `insider_ownership_pct` y `ceo_tenure_years` solo con cita verificada contra fuentes
     primarias (DEF 14A, 10-K Item 10, Form 4) que contenga el número / el año de nombramiento;
     `backlog_usd` solo con `backlog_citation` verificada con la cifra (`gate_llm_value`).
     Tavily y transcripts sirven para citas de texto, que se re-etiquetan con la fuente real
     (`validate_and_relabel`), nunca para verificar un número.
   - Consumidores: el contexto del Value Expert y "Sources & Citations" solo usan citas
     verificadas; las rechazadas se listan aparte ("Unverified citations (excluded from
     synthesis)"); la narrativa libre del LLM se etiqueta "(LLM summary, unverified)".

## Consequences

- (+) Los fallos son visibles y tipados; el fallback ya no oculta bugs propios.
- (+) El coste por run incluye Claude y el informe puede decir qué modelo respondió.
- (+) Un fallo técnico ya no se archiva como decisión de inversión: sin VERDICT el report es
  `-INCOMPLETE.md` y la fila SQLite `status='incomplete'` (EC-68).
- (−) `complete()` se mantiene como camino legacy (con su `except Exception`); desde Task 2
  ningún agente lo usa. Se elimina en H2.
- (−) Un valor fuera de contrato (p.ej. moat "unknown") ya no se corrige en silencio a "none":
  es un `LLMSchemaError` y el especialista queda `stub=True`. Más fallos visibles a cambio de
  ningún dato inventado.

## Alternatives considered

- `instructor` u otra librería de structured outputs: dependencia nueva sin ganancia sobre los
  helpers nativos de los SDK instalados (YAGNI).
- JSON mode + parser propio: es lo que falló.
- `anthropic.messages.parse`: oculta `stop_reason` (ver Decision 1).
