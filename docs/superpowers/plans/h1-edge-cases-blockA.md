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
