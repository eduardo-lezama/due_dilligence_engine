# EDGE_CASES — Bloque A (H1, T2–T4) — pendiente de merge en `EDGE_CASES.md`

Formato: síntoma → causa raíz → fix → commit. Escrito en la worktree `h1-blockA`
(ruling R2: no se edita `EDGE_CASES.md` para evitar conflictos; la sesión principal
lo fusiona). El hash de commit es el de la rama `h1-blockA`.

---

### EC-68 — Fallos de parseo del LLM disfrazados de contenido real ("HOLD fantasma") (T2b–T2e)

- **Síntoma:** cuando el LLM devolvía JSON truncado o mal formado, el report del especialista salía con `stub=False` y el texto `"[Parse error] Raw LLM response: ..."` dentro de `mda_highlights` (filings) o campos vacíos (management, competitive, critic). El Value Expert, ante el mismo fallo, producía un `verdict="HOLD"` de relleno y el archivo se llamaba `...-HOLD.md` / `...-PENDING.md`. Un fallo técnico se archivaba como una decisión de inversión.
- **Causa raíz:** (1) `complete()` devolvía texto libre y cada agente recortaba el JSON con `find("{")`/`rfind("}")` + `json.loads` dentro de un `except Exception` que nunca propagaba; (2) el report no tenía forma de expresar "corrió y falló" (solo `stub`); (3) `complete()` hacía fallback de proveedor ante cualquier excepción, incluidas truncaciones.
- **Fix:** `LLMClient.structured()` con structured outputs nativos y errores tipados (`llm/errors.py`, T1); schemas de salida estrictos en `schemas/llm_outputs.py` (T2a); cada agente captura `LLMError` y devuelve `stub=True, error="<Clase>: msg"` (T2b filings, T2c management/competitive/critic, T2d Value Expert sin VERDICT y con un único reintento solo ante `LLMSchemaError`); archiver nombra el report `-INCOMPLETE.md` y lista los fallos; CLI muestra panel rojo "ANALYSIS INCOMPLETE" (T2e). ADR-032.
- **Commit:** `feat(h1-T2b)` (filings) … `feat(h1-T2e)` en la rama `h1-blockA`.

### EC-69 — Citas nunca validadas y números del LLM presentados como dato (T3a–T3c)

- **Síntoma:** el invariante 2 (CLAUDE.md) y ADR-005 decían que toda cita se validaba por substring-match contra el filing; en el código no existía ningún validador. Las citas llegaban al report y al contexto del Value Expert tal como las emitía el LLM (incluidas paráfrasis y excerpts inventados). Además `insider_ownership_pct`, `ceo_tenure_years`, `form4_transactions_12m` y `backlog_usd` los generaba el LLM y se presentaban como datos ("Insider ownership (DEF 14A)"), y `insider_transaction_pattern` —texto libre del LLM— se etiquetaba "(Form 4)" (origen del caso KBR CEO/CFO).
- **Causa raíz:** la validación post-hoc se diseñó en Fase 0 (ADR-005) pero nunca se implementó; los agentes construían `Citation` directamente desde el JSON del LLM, y los schemas permitían al LLM rellenar campos numéricos sin evidencia.
- **Fix:** `validation/citations.py` (T3a): substring normalizado (NFKC, comillas/guiones tipográficos, minúsculas, espacios) o `rapidfuzz.partial_ratio ≥ 90`; todos los números del excerpt deben estar en la ventana alineada; excerpt < 20 caracteres → rechazado; corpus vacío → todas rechazadas con nota. Rechazadas: `verified=False`, `confidence=0`, `verification_note`. `gate_llm_value`: un número del LLM solo se conserva si su cita se verifica Y contiene el número (a escala ×1/×1e3/×1e6/×1e9). T3b: validación dentro de cada nodo con el corpus local; `form4_transactions_12m = len(form4_transactions)` (determinista); ownership y tenure solo contra fuentes primarias (DEF 14A, 10-K Item 10, Form 4), nunca Tavily/transcripts; `source_url` normalizado a la etiqueta real del corpus. T3c: el contexto del Expert y la sección "Sources & Citations" solo muestran citas verificadas; las rechazadas van a "## Unverified citations (excluded from synthesis)"; narrativa LLM etiquetada "(LLM summary, unverified)".
- **Pendiente (H2):** validación semántica claim↔excerpt (un excerpt real puede no sostener el claim, p.ej. CEO vs CFO).
- **Commit:** `feat(h1-T3a)` … `feat(h1-T3c)` en la rama `h1-blockA`.

### EC-70 — Datos extraídos que nunca llegaban a la síntesis (T4a–T4c)

- **Síntoma:** `governance_red_flags` (EC-66: investigaciones SEC, material weaknesses, restatements, class actions) se extraía del 10-K pero ningún consumidor lo leía: ni el Value Expert, ni el Critic, ni el report. Las respuestas socráticas del checkpoint 1 (`answers`) no llegaban al Expert, y `growth_justification` (checkpoint 2) solo aparecía si existía `historical_cagr_5y`, mezclada con la evidencia en el bloque HISTORICAL CONTEXT.
- **Causa raíz:** sin contrato productor→consumidor: un campo nuevo en un report no obliga a nadie a consumirlo, y ningún test lo detectaba. Las reglas de prompt (23/24) no tenían check determinista.
- **Fix:** T4a — bloque `## GOVERNANCE RED FLAGS (from 10-K)` en el contexto del Expert y en el prompt del Critic, sección `## Governance Red Flags (10-K)` en el report, regla 23 en el prompt; `FilingsLLMOutput.active_sec_investigation`/`material_weakness` + `governance_citation`: los flags `True` solo sobreviven con cita verificada y activan un tope determinista `verdict_confidence ≤ 0.6` (R1, confirmado por Eduardo); check post-output: si §7 no nombra un flag verificado → caveat "⚠️ §7 omits governance flag". T4b — `_format_investor_hypotheses`: bloque `## INVESTOR'S OWN HYPOTHESES (evaluate critically, do not adopt)` al final del contexto con respuestas no vacías y `growth_justification` (aunque no haya CAGR); regla 24 + check determinista de etiquetas SUPPORTED/CONTRADICTED/UNTESTABLE en §8. T4c — guard test `tests/test_schema_consumers.py`: todo campo de los reports especialistas y de `FinancialsReport`/`CriticReport` tiene consumidor (value_expert, critic, archiver, cli) o una excusa documentada en `_ALLOWLIST`.
- **Commit:** `feat(h1-T4a)` … `feat(h1-T4c)` en la rama `h1-blockA`.

### Corrección a EC-53 (T4c) — para aplicar en `EDGE_CASES.md` línea ~387

- Sustituir "Rule #18 en `_SYSTEM_PROMPT` del value_expert" por "Rule #19 en `_SYSTEM_PROMPT` del value_expert (numeración corregida en H1; la 18 es PEER RELATIVE VALUATION)". El código decía "Per Rule 18:" en el bloque POST-10-K ACQUISITION; corregido a "Per Rule 19:" y fijado por `tests/test_schema_consumers.py::test_rule_numbering_post_10k_acquisition_is_19`.

---

## Propuestas de texto para archivos gitignored (ruling R3 — los aplica la sesión principal)

Los archivos siguientes están en `.gitignore` y no existen en la worktree; el plan pide editarlos en T3a.

**`CLAUDE.md` raíz, invariante 2** — sustituir "Validación post-hoc por substring-match contra el filing cacheado." por:

> Validación post-hoc en `validation/citations.py`: substring normalizado o `rapidfuzz.partial_ratio ≥ 90` contra el texto fuente que vio el LLM, y todos los números del excerpt presentes en la ventana alineada. Los números que emite el LLM solo se conservan con cita verificada contra fuente primaria (DEF 14A, 10-K, Form 4) que contenga el número; si no, `None`.

**`AGENTIC_PATTERNS.md` §11, sección "El flujo" y "Por qué funciona"** — sustituir el pseudocódigo `excerpt in document_text` por:

> 1. El agente construye un corpus local `{etiqueta: texto}` con exactamente lo que envió al LLM (no entra en el estado).
> 2. `validate_citations(citations, corpus)`: normaliza (NFKC, comillas/guiones ASCII, minúsculas, espacios); substring exacto → ventana = excerpt; si no, `partial_ratio_alignment` ≥ 90 → ventana = tramo alineado del corpus; si no, rechazo.
> 3. Todos los números del excerpt deben aparecer en esa ventana (un "100,000" inventado no se justifica con un "100,000" de otra página).
> 4. Rechazo → `verified=False`, `confidence=0`, `verification_note`; se muestra aparte en el report ("Unverified citations") y nunca entra en el contexto del Value Expert.
> 5. Números del LLM: `gate_llm_value(value, cita, corpus, numeric=True)` → solo si la cita se verifica y contiene el número (×1/×1e3/×1e6/×1e9).
>
> Límite conocido: demuestra que el texto citado existe, no que el claim se siga del excerpt (H2).

**`src/due_diligence/agents/CLAUDE.md`, regla "Every Citation must pass substring-match validation..."** — sustituir por:

> Every Citation must pass `validation.citations.validate_citations` inside the node (corpus = the exact source text sent to the LLM). Numeric fields emitted by the LLM go through `gate_llm_value` against primary sources only; counts that can be computed (Form 4) are computed, never asked to the LLM. LLM calls use `get_client().structured(Schema, ...)`; on `LLMError` return `Report(stub=True, error=error_label(e))` (ADR-032).
