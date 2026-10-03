# EDGE_CASES — Bloque B (H1, T5–T9) — pendiente de merge en `EDGE_CASES.md`

Formato: síntoma → causa raíz → fix → commit. Escrito en la worktree `h1-blockB`
(ruling R2: no se edita `EDGE_CASES.md` para evitar conflictos; la sesión principal
lo fusiona). El hash de commit es el de la rama `h1-blockB`.

---

### EC-78 — `_get_point` devolvía un año antiguo etiquetado como el actual (T5)

- **Síntoma:** un concepto de balance sin dato en el último ejercicio (columna `FY 2025` = NaN) aparecía en el report con el valor de FY2024 pero etiquetado `FY2025`. Deuda/caja/activos de un año antes presentados como actuales, sin aviso.
- **Causa raíz:** `_get_point(df, concept, period)` iteraba las columnas FY y devolvía el **primer valor no-NaN**, poniéndole la etiqueta `period` pedida. El parámetro `period` solo se usaba como etiqueta, nunca para seleccionar.
- **Fix:** `xbrl_extract.point_value(df, concepts, period, facts)` → `select_period_value` lee la columna EXACTA del periodo; si falta, raw facts emparejados por `period_end` (`EdgarFactsLookup`); si tampoco, `None` (nunca un año distinto). Test `test_point_value_respects_period_instead_of_returning_stale_year`.
- **Commit:** ver `git log h1-blockB` → `feat(h1-T5)`.

### EC-79 — "CAGR 5 años" calculado con 3 años: `periods=4` por defecto (T5)

- **Síntoma:** `revenue_cagr_5y` y las series anuales nunca tenían más de 4 años; el "CAGR 5y" era en realidad de 3 intervalos.
- **Causa raíz:** `facts.income_statement()/balance_sheet()/cashflow_statement()` tienen `periods: int = 4` (`edgar/entity/entity_facts.py:1671-1672, 1733-1734, 1841-1842`). El `n=10` de `_get_annual` nunca tuvo efecto porque el DataFrame ya venía recortado.
- **Fix:** `fetch_raw` pide `periods=10` a los tres estados. (T7 fija las ventanas de los consumidores con `max_years=5` para no cambiar silenciosamente los defaults a CAGRs de 9 años.)
- **Commit:** `feat(h1-T5)`.

### EC-87 — `fetch_raw` podía usar un 10-K/A y una etiqueta FY distinta a la de las series (T5)

- **Síntoma:** `source_filing`/etiquetas del balance calculadas como `FY{period_of_report[:4]}` desde `filings.latest()`, que incluye 10-K/A; con cierres a principios de enero (52/53 semanas) la etiqueta no coincidía con la columna de edgartools; nada comprobaba que el "último 10-K" y la columna más reciente de los estados fueran el mismo año.
- **Causa raíz:** (1) `filings.latest()` en lugar de `_get_latest_full_10k` (EC-27 solo se aplicó a texto, no a números); (2) etiqueta FY por los 4 primeros caracteres del `period_of_report` en lugar de la regla de edgartools (cierre 1–7 enero → año anterior); (3) sin invariante entre ambos.
- **Fix:** `_get_latest_full_10k` en `fetch_raw`; `fy_label_from_period_end` replica la regla de edgartools 5.30 (`enhanced_statement.py:1756-1772`); `build_raw` lanza `DataUnavailableError` si la etiqueta del 10-K ≠ la columna más reciente del income statement. Tests `test_fy_label_from_period_end_mirrors_edgartools`, `test_build_raw_fails_loud_when_statements_latest_column_differs_from_10k`.
- **Commit:** `feat(h1-T5)`.

### Nota para `src/due_diligence/tools/CLAUDE.md` (gitignored → aplicar en la sesión principal)

Añadir bajo "Rules":

> - Excepción documentada a "No bare dict returns": `xbrl_extract.build_raw`, `resolve_debt`, `resolve_cash` y el `balance_history` devuelven dicts porque el contrato `raw` (fetch_raw → parse_financials) ya es un dict por diseño (los fixtures JSON de `tests/fixtures/` son ese dict). Todo valor lleva `{"value", "period", "source"}`; un 0.0 por defecto lleva `"source": "absent"`.
> - Lógica de selección XBRL → `xbrl_extract.py` (pura, testeada con DataFrames sintéticos). `edgar.py` solo hace red.
