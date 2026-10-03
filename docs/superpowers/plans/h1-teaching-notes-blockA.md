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
