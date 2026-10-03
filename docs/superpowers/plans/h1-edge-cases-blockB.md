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

### EC-80 — Deuda doble-contada o infravalorada (T6)

- **Síntoma:** `total_debt` inflado en empresas que reportan a la vez `LongTermDebt` (que YA incluye la porción corriente) y `LongTermDebtCurrent`/`DebtCurrent`; o infravalorado cuando la deuda corriente venía solo como `CommercialPaper` (no estaba en la cadena) o cuando el LTD venía como `...AndCapitalLeaseObligations` y además se sumaban leasings.
- **Causa raíz:** la cadena ST sumaba TODOS los conceptos presentes (`ShortTermBorrowings + LongTermDebtCurrent + ... + DebtCurrent`), mezclando un total (`DebtCurrent`) con sus componentes; la cadena LT tomaba `LongTermDebt` (total incl. corriente) como "largo plazo". Un total se sumaba con sus componentes.
- **Fix:** `xbrl_extract.resolve_debt` con política exclusiva: LT = noncurrent explícito, si no `LongTermDebt − porción corriente` (inferida de `DebtCurrent − borrowings` si no hay `LongTermDebtCurrent`, con flag); ST = `DebtCurrent` si existe, si no primer borrowing (exclusivos) + porción corriente; leasing financiero solo si el concepto de deuda no lo incluye. Identidades (`LongTermDebt ≈ noncurrent + current`, `DebtCurrent ≥ LongTermDebtCurrent`) se reportan en `data_quality_flags`. `total_debt = LT + ST + finance_leases`.
- **Ejemplo AAPL-like:** LTD 90, noncurrent 78, current 12, CP 8 → antes ST = 12 (+ CP ausente) y LT = 90 → 102; ahora 78 + (12 + 8) = 98.
- **Commit:** `feat(h1-T6)`.

### EC-81 — Caja = 0 en silencio, STI hardcodeado a 0 y la doble resta de STI evitada (T6)

- **Síntoma:** (1) si ningún concepto de caja estaba en la vista curada, `cash_and_equivalents = 0.0` sin aviso → EV e IC sobrestimados; (2) `short_term_investments` siempre 0 (AAPL tiene ~$35B de valores negociables corrientes); (3) `cash_and_equivalents = cash + STI` en `parse_financials`, de modo que un bridge que restase STI aparte la habría restado dos veces.
- **Causa raíz:** defaults silenciosos (`{"value": 0.0}`) en lugar de fail loud; STI marcado como "no aparece en el BS normalizado" sin buscar conceptos alternativos ni raw facts; semántica de `cash_and_equivalents` ambigua.
- **Fix:** `resolve_cash` (estándar → restricted cash marcado en `source` → `DataUnavailableError`); STI/LTI extraídos con cadenas propias y raw facts; `cash_and_equivalents` = SOLO caja, `short_term_investments`/`long_term_investments` campos propios; consumidores actualizados (Value Expert "Cash (ex-STI)" + línea STI, CLI ídem, Critic `net_debt = deuda − caja − STI`); `balance_identity_flags` (Assets == L+SE estricta → raise; Assets ≈ L + E → flag). CLI muestra `DataUnavailableError` como panel rojo "DATA UNAVAILABLE" (exit 1).
- **Nota:** `valuation.py` y `peers.py` siguen usando cash-only en su EV hasta T12 (comportamiento idéntico al previo porque la STI era siempre 0); anotado inline.
- **Commit:** `feat(h1-T6)`.

### EC-82 — FCF emparejaba OCF y capex de años distintos (T7)

- **Síntoma:** con una serie con hueco (BBW: capex con FY2025 y OCF sin él, o viceversa), `FCF[FY2024] = OCF[FY2024] − capex[FY2025]`; márgenes brutos/operativos de Value Expert, Critic y Competitive con el mismo problema.
- **Causa raíz:** `zip(ocf_list, capex_list)` empareja por POSICIÓN; asume que ambas series tienen exactamente los mismos años en el mismo orden.
- **Fix:** `utils/periods.align_by_period` (empareja por etiqueta FY, omite periodos sin par) en `parse_financials` (FCF), `value_expert` y `critic` (margen bruto), `competitive` (márgenes bruto y operativo). Capex con `abs()` / `_mv_list_abs` (edgartools 5.5x lo devuelve con signo). Test `test_align_by_period_never_pairs_different_years`, `test_margins_aligned_by_period_not_position`.
- **Commit:** `feat(h1-T7)`.

### EC-83 — CAGR por distancia de índice, no por años fiscales (T7)

- **Síntoma:** BBW (falta FY2025): revenue `[FY2026, FY2024, FY2023, FY2022]` → el índice 3 se trataba como 3 años cuando son 4 → CAGR sobrestimado. Mismo patrón en el FCF CAGR (`valuation._fcf_cagr_with_window`) y en el criterio "Revenue 3y CAGR" de Competitive (`revenue[3]`).
- **Causa raíz:** exponente `1/índice` en lugar de `1/(año_reciente − año_antiguo)`.
- **Fix:** `utils/periods.cagr_by_fiscal_years(series, max_years, scan_for_positive_start)`; `financials._compute_revenue_cagr_5y` → ventana de 5 años fiscales reales (decisión de producto: antes era un "5y" de como mucho 4 intervalos); `_fcf_cagr_with_window` → `max_years=5` + scan EC-60; Competitive → ventana de 3 años fiscales exactos. Ventanas de consumidores fijadas por test (`tests/test_series_windows.py`): `rev_median` `[:5]`, tendencia ROIC del Value Expert `roic_series[min(4, len-1)]`, ROIC de Competitive `[:5]`, `_ebit_median` `[:5]`.
- **Commit:** `feat(h1-T7)`.

### EC-84 — El snapshot trimestral nunca se producía + test tautológico (T7; test en T5)

- **Síntoma:** `quarterly_snapshot` siempre ausente; log `quarterly_snapshot_no_quarters` en todos los runs; el contexto del Value Expert nunca tuvo datos del 10-Q aunque el 10-K tuviera > 45 días.
- **Causa raíz:** `get_quarterly_financials()` devuelve el XBRL del 10-Q, cuyo DataFrame tiene `concept` como **columna** con prefijo (`us-gaap_Revenues`) y columnas de periodo `"2026-06-27 (Q3)"`/`"(YTD)"` (verificado en vivo). El código filtraba `c.startswith("Q")` y buscaba conceptos en el índice → siempre vacío. Secundario: orden por string (`'Q4 2025' > 'Q1 2026'`). El test `test_stale_10k_beyond_45_days_triggers_fetch` afirmaba `get_filings.called or result is None` → pasaba siempre.
- **Fix:** `xbrl_extract.latest_quarter_column` (ordena por fecha, ignora YTD, acepta ambos formatos) + `quarter_value` (normaliza `concept` columna→índice sin prefijo, excluye filas dimensionales); revenue con la misma cadena que el anual (`REVENUE_CONCEPTS`). Test con el formato real; test tautológico reescrito (`assert_called_once_with()` + `result is None`).
- **Efecto colateral detectado:** los tests que corrían `financials_node` con `fetch_raw` mockeado hacían una llamada REAL a EDGAR para el trimestral (antes acababa en `None` sin que se notara). Ahora devolvía datos vivos → `conftest.py` añade un autouse que mockea `fetch_quarterly_snapshot` salvo en tests marcados `@pytest.mark.real_quarterly_fetch`.
- **Commit:** `feat(h1-T7)`.

### EC-88 — Huecos de años en series anuales (BBW sin FY2025) (T7)

- **Síntoma:** series anuales con años interiores ausentes (BBW FY2025) aunque el dato existe en companyfacts.
- **Causa raíz:** el validador fiscal_year/period_end de edgartools (`enhanced_statement.py:1548`) o el umbral de < 5 facts descartan la columna del año en la vista curada.
- **Fix:** `xbrl_extract.backfill_gaps(series, facts)`: rellena solo años INTERIORES desde raw facts del mismo concepto emparejando por `period_end` (`EdgarFactsLookup`), con `source = "facts:<concepto>"`; no extrapola ni toca series derivadas. `build_raw` lo aplica a cada serie anual.
- **Pendiente conocido:** restatements — el backfill toma el filing más reciente y puede mezclar años re-expresados con no re-expresados.
- **Commit:** `feat(h1-T7)`.

### EC-85 — ROIC histórico con IC y tasa del último año (T9)

- **Síntoma:** la serie ROIC de 4 años dividía el NOPAT de cada año entre el IC del ÚLTIMO balance y aplicaba la tasa impositiva del último año a todos: una empresa que duplicó su capital aparecía con ROIC histórico inflado (capital de hoy, beneficios de ayer) y la "tendencia" era casi solo la del EBIT. El contexto del Value Expert llamaba "marginal ROIC > average" a esa tendencia.
- **Causa raíz:** el raw solo tenía el balance del último año (no había `balance_history`) y `parse_financials` calculaba un único `effective_tax_rate` local.
- **Fix:** `build_raw` emite `balance_history` (un balance por columna FY con las mismas reglas de deuda/caja/STI); `parse_financials` calcula `ROIC_t = NOPAT_t / media(IC_t, IC_{t−1})` con la tasa del año (`effective_tax_rate` canónica importada de `xbrl_extract`, sin variable local homónima); `edgar.invested_capital(...)`: primaria Equity + Debt − Cash − STI − LTI, operativa Assets − Cash − STI − LTI − ROU − NIBCL, **un solo método por empresa** (si algún año tiene equity ≤ 0 → operativa para toda la serie), IC < 5 % de activos → n/m; `incremental_roic` = ΔNOPAT/ΔIC ≤ 3 años (None si ΔIC ≤ 5 %); series `total_debt_series`, `cash_series` (caja + STI), `equity_series` para `suggest_wacc` (T11). Regla 22 del Value Expert reescrita sobre el ROIC incremental real; la tendencia ya no se etiqueta como "ROIC marginal". Fixtures legacy sin `balance_history` → `source="calculated-latest-ic"` hasta el re-fetch de T14 (AAPL 0.874 intacto).
- **AZO:** IC operativo explícito = 19,355,324,000 − 271,803,000 = 19,083,521,000 (mismo número que el antiguo "total_assets-cash fallback", ahora método `operating:`); test en `test_edgar_archetypes.py`.
- **Commit:** `feat(h1-T9)`.

### Pendientes conocidos del Bloque B

- Restatements: `backfill_gaps`/`EdgarFactsLookup` toman el filing más reciente; pueden mezclar años re-expresados con no re-expresados.
- `operating_income` trimestral solo con `OperatingIncomeLoss` (sin fallback GP − SG&A).
- `valuation.py` y `peers.py` siguen calculando EV con cash-only (sin restar STI) hasta el bridge de T12; idéntico al comportamiento previo porque la STI era siempre 0.
- `valuation.py:236` (NOPAT proxy) aún usa su propio tipo impositivo: T10 lo sustituye por `effective_tax_rate_multi`.

### Nota para `src/due_diligence/tools/CLAUDE.md` (gitignored → aplicar en la sesión principal)

Añadir bajo "Rules":

> - Excepción documentada a "No bare dict returns": `xbrl_extract.build_raw`, `resolve_debt`, `resolve_cash` y el `balance_history` devuelven dicts porque el contrato `raw` (fetch_raw → parse_financials) ya es un dict por diseño (los fixtures JSON de `tests/fixtures/` son ese dict). Todo valor lleva `{"value", "period", "source"}`; un 0.0 por defecto lleva `"source": "absent"`.
> - Lógica de selección XBRL → `xbrl_extract.py` (pura, testeada con DataFrames sintéticos). `edgar.py` solo hace red.
