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

---

## T2c — Un patrón para todos los agentes LLM (management, competitive, critic)

**Concepto.** Los cuatro especialistas LLM comparten ahora la misma forma: `client.structured(Schema, ...)` → `_build_report(out, ticker)` (mapeo puro) → `model_copy(model_used=...)`; `except LLMError` → `Report(stub=True, error=error_label(e))`. Lo común vive en `agents/llm_support.py` (`error_label`, `model_used`).

**Por qué.** Un patrón idéntico permite que el archiver, la CLI y SQLite traten los fallos de forma genérica (`collect_failures` lee `.error` de cualquier report) y hace imposible que un agente se quede con el parseo viejo. Tres detalles de dominio:
- **Management:** `form4_transactions_12m` ya no lo emite el LLM; es `len(form4_transactions)` (ADR-005). Por eso el test de campos pasa de 5 (lo que decía el LLM) a 1 (lo que hay en el fixture).
- **Competitive:** `Literal["wide","narrow","none"]` en el schema sustituye a "valor desconocido → none". Un valor fuera de contrato ya no se convierte en silencio en "none": es un `LLMSchemaError`. El clamp de wide moat (ADR-024) sigue intacto.
- **Critic:** si la síntesis no existe o es stub, no llama al LLM (`error="skipped: synthesis unavailable"`). Un "red team" contra un análisis ausente solo añade ruido y coste.

**Relación con la arquitectura.** ADR-032; invariante 3 (los agentes solo importan `llm.errors`, nunca el SDK); invariante 6 (los prompts mantienen las preguntas de §3 y §2; solo pierden el bloque de formato JSON).

**Ejemplo.** `critic_agent({"ticker": "T", "synthesis": ValueExpertReport(ticker="T", stub=True)})` → `CriticReport(stub=True, error="skipped: synthesis unavailable")` y `structured` nunca se llama.

---

## T2d — Sin VERDICT es mejor que un HOLD inventado (Value Expert + validador ADR-031)

**Concepto.** Un veredicto es una decisión de inversión; un fallo técnico no lo es. Cuando el LLM no produce una síntesis válida, el sistema debe decir "no hay veredicto", no "HOLD".

**Por qué.** `_parse_response` devolvía `verdict="HOLD", verdict_confidence=0.0` ante cualquier JSON inválido. En la auditoría 44/51 veredictos eran HOLD: parte eran fallos de parseo archivados como decisiones (EC-68). Además el validador de thesis-break (ADR-031) devolvía `False` ante un error, lo que equivalía a "la tesis no está rota" sin haberlo comprobado.

**Cómo.**
- `structured(ValueExpertLLMOutput, ...)` con un bucle de 2 intentos: **solo `LLMSchemaError` se reintenta** (es estocástico); `LLMTruncatedError` y `LLMProviderError` no mejoran repitiendo y van directos a `_failed_synthesis` (`stub=True`, `error`, `verdict=None`).
- `_to_report(out, ticker)` conserva los clamps deterministas: confidence [0,1], sizing [0,20] y sizing ≤ confidence×10 (EC-24).
- `_validate_thesis_break_with_llm` usa `structured(ThesisBreakCheck)` y devuelve `bool | None`; con `None` `_thesis_break_self_check` añade "⚠️ Thesis-break validator unavailable; manual check required".
- El prompt pierde el "OUTPUT SCHEMA" en JSON, que pasa a "SECTION GUIDANCE" (contenido por campo). La regla 14 se reescribe (formato impuesto por la API) **sin renumerar**: la numeración la referencian tests, EC y la propia regla 19.

**Relación con la arquitectura.** ADR-009 (VERDICT obligatorio cuando `stub=False` — ahora también: nunca VERDICT cuando `stub=True`), ADR-031, ADR-032.

**Ejemplo.** `structured.side_effect = [LLMSchemaError("bad"), _ve_out()]` → 2 llamadas y `verdict="BUY"`; `side_effect = LLMTruncatedError("t")` → 1 llamada, `stub=True`, `verdict is None`.

---

## T2e — Visibilidad del fallo de punta a punta (archiver, CLI, SQLite, contexto del Expert)

**Concepto.** Que un agente devuelva `error` no sirve de nada si ninguna capa lo enseña. "Fail loud" tiene que llegar hasta el archivo, la base de datos, la terminal y el propio razonamiento del Expert.

**Por qué.** Antes un run sin VERDICT se archivaba como `-PENDING.md` con `| **VERDICT** | **N/A** |` y nada más; la CLI mostraba "degraded" en checkpoint 1 sin motivo; SQLite no distinguía runs completos de incompletos ni sabía qué modelo respondió. La métrica de éxito de H1 ("% de runs incompletos", "citas verificadas por run") no era medible.

**Cómo.**
- `archiver.report_filename(today, ticker, synthesis)` → `...-INCOMPLETE.md` sin VERDICT. Un solo sitio: la CLI lo reutiliza para encontrar el archivo (antes duplicaba la lógica con `"PENDING"`). La variable local pasa a `fname` para no sombrear la función.
- `collect_failures(state)` lee `.error` de todos los reports (`getattr`: también financials/valuation cuando lo tengan) → sección "## Analysis incomplete" tras el header, panel rojo `ANALYSIS INCOMPLETE` en la CLI (checkpoint 1 y antes del VERDICT) y bloque `## DATA GAPS` al principio del contexto del Expert.
- `_to_report(out, ticker, state)`: si falta Filings o Management (ausente, stub o con error) → `verdict_confidence ≤ 0.6`, y el clamp de sizing (≤ confidence×10) se aplica **después** para seguir siendo cierto.
- SQLite: `status`, `models_used`, `n_verified_citations`, `methodology_version="H1"` con migración idempotente (`ALTER TABLE ... ADD COLUMN` dentro de `try/except OperationalError`). `_insert_row` acepta llamadores antiguos (columnas H1 → NULL).
- "Models used" en el pie del markdown (`RunStats.calls_by_model`) y línea "Models: gpt-4.1 ×3, ..." en el bloque Performance.

**Relación con la arquitectura.** ADR-032 pasa a Accepted. ADR-009: el VERDICT es obligatorio cuando hay síntesis; cuando no la hay, el sistema lo dice en cada capa. ADR-021: la contabilidad de coste ahora incluye el modelo real.

**Ejemplo.** Estado con `synthesis=ValueExpertReport(stub=True, error="LLMTruncatedError: x")` → archivo `2026-10-03-TST-INCOMPLETE.md`, fila `status='incomplete'`, `verdict=NULL`, y en la CLI: "No VERDICT was produced. Do not act on this report."

---

## T3a — Validación de citas (`validation/citations.py`)

**Concepto.** Una cita es una promesa: "esta frase está en el documento". Validarla es comprobar la promesa contra el texto que el LLM realmente leyó.

**Por qué.** ADR-005 lo prometía desde Fase 0, pero no existía ningún validador (EC-69). Un substring exacto es demasiado estricto (los LLM cambian comillas, espacios, "per share" ↔ "a share") y un fuzzy puro es demasiado laxo con los números: "100,000 shares" casa al 97 % con "10,000 shares".

**Cómo.** Dos filtros encadenados:
1. *Existencia del texto:* normalizar (NFKC, tipografía → ASCII, minúsculas, espacios); substring exacto o `rapidfuzz.fuzz.partial_ratio_alignment ≥ 90`, que además devuelve **dónde** casa (`dest_start`, `dest_end`).
2. *Números en la ventana:* todos los números del excerpt deben aparecer en ese tramo alineado del corpus, no en cualquier parte del documento.
Excerpts de menos de 20 caracteres se rechazan (con 6 caracteres partial_ratio encuentra coincidencias triviales). Corpus vacío → todo rechazado con nota explícita. `gate_llm_value` aplica la regla a números del LLM: solo sobreviven con cita verificada que contenga el número a alguna escala (los 10-K escriben "(in millions) $1,270").

**Relación con la arquitectura.** Implementa por fin el invariante 2 y ADR-005 (nota de corrección añadida). Es determinista y sin red: los agentes lo llaman dentro del nodo, tras `structured()` (T3b).

**Ejemplo.** Corpus "the CEO purchased 10,000 shares at $25.50 per share": la cita "...purchased 100,000 shares..." casa por fuzzy, pero `100000` no está en la ventana → `verified=False`, nota `numbers not in source window: ['100000']`.

---

## T3b — Números del LLM: calcular, citar o descartar

**Concepto.** Jerarquía para cualquier cifra que aparezca en el análisis: (1) si se puede **calcular** a partir de datos descargados, se calcula; (2) si solo la puede extraer el LLM de un texto, debe venir con una **cita verificada contra fuente primaria** que contenga esa cifra; (3) si no, se **descarta** (`None`).

**Por qué.** El LLM devolvía `form4_transactions_12m`, `insider_ownership_pct`, `ceo_tenure_years` y `backlog_usd` sin evidencia, y se presentaban como datos (EC-69). Peor: un % de propiedad sacado de un artículo web podía acabar etiquetado "DEF 14A".

**Cómo.**
- Cada nodo construye su corpus local con el texto exacto enviado al LLM: filings `{10-K Item 1, 1A, 7}`; management `{DEF 14A, 10-K Item 10, Form 4, 8-K 5.02, Web (Tavily), Transcripts}`; competitive `{Financial data, Web (Tavily)}`.
- `management._build_report(out, ticker, corpus, form4_count)`: `form4_count = len(form4_transactions)`; ownership y tenure pasan por `gate_llm_value` contra el **sub-corpus primario** (DEF 14A, 10-K Item 10, Form 4); tenure exige además un año `(19|20)\d{2}` en el excerpt.
- `filings._build_report(out, ticker, corpus)`: `backlog_usd` solo con `backlog_citation` verificada que contenga la cifra.
- Citas de texto libre: `validate_and_relabel` las valida contra todo el corpus y cambia `source_url` a la etiqueta real donde se encontró ("Web (Tavily)"), no a la que dijo el LLM. Las no verificadas se conservan, marcadas.
- Log `<agent>_citations_validated(verified, unverified)`; el total verificado va a la columna SQLite `n_verified_citations` (T2e).

**Relación con la arquitectura.** ADR-005 (zero hallucination numérica), ADR-011 (primary-source-first), ADR-032 decisión 7.

**Ejemplo.** DEF 14A: "...as a group beneficially own 12.5% of outstanding shares." LLM dice 12.5 con esa cita → se conserva. LLM dice 30.0 con la misma cita → `None` ("value not present in excerpt"). LLM dice 12.5 citando un blog → `None`: el blog no es fuente primaria.
