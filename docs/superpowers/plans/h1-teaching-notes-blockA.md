# Notas didácticas — Bloque A (H1, T2–T4) — pendiente de merge en `DETAILED_EXPLANATION.md`

Formato: concepto → por qué → cómo → relación con la arquitectura → ejemplo del código.
(Ruling R2: la sesión principal fusiona estas notas en `DETAILED_EXPLANATION.md`.)

---

## T2a — Contrato LLM frente a contrato de dominio (`schemas/llm_outputs.py`)

**Concepto.** Hay dos contratos distintos: lo que pedimos al proveedor LLM (el *schema de salida*) y lo que el resto del sistema consume (el *report de dominio*). Antes eran el mismo objeto, y el agente rellenaba el report a mano desde un `dict` parseado con `json.loads`.

**Por qué.** OpenAI *strict* `json_schema` exige que todas las propiedades sean `required`, `additionalProperties: false` y sin `default`. Los reports de dominio necesitan justo lo contrario: defaults (`stub=True`, `error=None`) para deserializar checkpoints antiguos, campos deterministas que el LLM nunca debe emitir (`form4_transactions_12m`) y clamps de negocio. Un solo modelo para ambos papeles obliga a debilitar uno de los dos.

**Cómo.** `llm_outputs.py` define `_Strict` (`extra="forbid"`) y un schema por agente con todo `X | None` *requerido*. Las citas llegan como `CitationOut` (sin `ge/le`, que strict no garantiza) y `to_citation()` las convierte en `Citation` de dominio acotando `confidence` a [0, 1] y con `verified=False`. Los reports LLM heredan de `LLMAgentReport(ticker, stub, error, model_used)`.

**Relación con la arquitectura.** ADR-032 (structured outputs + fail loud) y ADR-005: una cita nace no verificada; solo `validation/citations.py` (T3) puede marcarla `verified=True`.

**Ejemplo.** `CitationOut(confidence=1.4).to_citation().confidence == 1.0`; `FilingsRAGReport(ticker="X").error is None` (un checkpoint antiguo sigue cargando).

---

## T2b — Fail-loud en agentes LLM (`filings_agent`)

**Concepto.** *Fail loud* significa que un fallo se representa como fallo en los datos (y por tanto en la UI), no como un valor por defecto que parece legítimo.

**Por qué.** Antes, si el JSON del LLM no se podía parsear, `_parse_llm_response` devolvía `FilingsRAGReport(stub=False, mda_highlights="[Parse error] ...")`. Para el resto del grafo eso era un análisis real: el Value Expert lo leía como "highlights del MD&A" y el informe no avisaba de nada (EC-68).

**Cómo.** `client.structured(FilingsLLMOutput, ...)` devuelve un objeto ya validado o lanza un `LLMError` tipado. El nodo hace `try/except LLMError` y en el `except` devuelve `FilingsRAGReport(stub=True, error="LLMTruncatedError: ...")`. `_build_report(out, ticker)` es una función pura de mapeo (sin parseo). `model_used` se lee de `client.last_model_used` (helper `agents/llm_support.model_used`). El prompt pierde el bloque "OUTPUT SCHEMA" en JSON: el formato lo impone el schema; el prompt solo describe el contenido de cada campo y exige `backlog_citation` literal.

**Relación con la arquitectura.** Invariante 5 (tools fallan loud) aplicado a la capa LLM; ADR-032. El grafo NO se detiene: el report con `error` viaja hasta el Value Expert (bloque DATA GAPS, T2e), el archiver (sección "Analysis incomplete") y la CLI (panel rojo).

**Ejemplo.** `client.structured.side_effect = LLMTruncatedError("trunc")` → `filings_agent(...)["filings"]` tiene `stub=True`, `error.startswith("LLMTruncatedError")` y `mda_highlights is None`.
