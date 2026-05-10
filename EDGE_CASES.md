# Edge Cases — Production Observations

Registro conciso de edge cases detectados en producción. Cada entrada: síntoma → causa raíz → estado.

**Regla**: cuando un run real revela un edge case, documentarlo aquí antes o junto al commit del fix.  
**NO duplicar** lo que ya está en `docs/mce-evolution.md` (trabajo arquitectónico diferido con trigger).

---

## Resueltos

### EC-01 — FCF vacío en empresas de servicios (PRDO, educación online)
- **Síntoma**: DCF N/A, "FCF negativo o insuficiente", aunque la empresa tiene EBIT y net income positivos.
- **Causa**: `zip(ocf_list, [])` produce lista vacía cuando capex no tiene concepto XBRL estándar. Empresas asset-light (educación, servicios) no reportan capex como línea separada.
- **Fix**: múltiples conceptos para OCF (4 fallbacks) y capex (4 fallbacks). Si capex está vacío pero OCF existe → `FCF ≈ OCF` marcado como `"calculated-no-capex"`. (`edgar.py`)
- **Commit**: `1355e89`

### EC-02 — DCF upside absurdo en cíclicos en pico (CALM, huevos/avian flu)
- **Síntoma**: DCF upside +3482%, intrínseco $2731/acción vs precio $76.
- **Causa**: FCF de pico cíclico como base × growth capeado al 30% × 10 años = absurdo. El sistema no advertía.
- **Fix**: warning visible en CLI y markdown cuando el raw FCF CAGR supera el cap (`⚠️ DCF growth capped at 30%`). El reverse DCF se marca como el número más informativo. (`valuation.py`, `archiver.py`, `cli.py`)
- **Commit**: `2e5453f`

### EC-03 — Value Expert JSON truncado a mitad (DRS, empresa de defensa)
- **Síntoma**: `[Parse error — raw response preserved]`, confidence=0, secciones vacías.
- **Causa**: `max_tokens=4096` insuficiente para el prompt v2 (más largo) con contexto rico (10-K denso). El JSON se corta antes de cualquier `}`.
- **Fix**: `max_tokens` del Expert subido a 8192. Log de `finish_reason='length'` en el cliente OpenAI para detectar truncaciones antes del parse error. (`value_expert.py`, `llm/client.py`)
- **Commit**: `53d2e02`

### EC-04 — Peer comparison mostraba "skipped by user" cuando sí se aportaron peers (ACMR, LULU)
- **Causa arquitectónica**: `competitive_agent` corre en el fan-out **antes** de `peers_node`. Cuando competitive buscaba `state["peer_financials"]`, el campo no existía todavía. `zip(roic_list, [])` → vacío silencioso.
- **Fix**: mover la construcción de `peer_roic_comparison` a `peers_node`, que corre después del checkpoint1 y tiene acceso a ambos: peers fetched + `state["competitive"]`. (`peers.py`, `competitive.py`)
- **Commit**: `b93f878`

### EC-05 — Mensaje "skipped by user" incorrecto cuando peers fallaron (LULU: ONON, UAA)
- **Síntoma**: el usuario aportó peers pero todos fallaron; el mensaje decía "skipped by user" en lugar de explicar qué pasó.
- **Fix**: tres mensajes distintos en archiver: (a) tabla si hay ROIC, (b) "fetched but no ROIC" si capex/ROIC no calculable, (c) "all fetch failed" si EDGAR devolvió error, (d) "skipped" solo si el usuario realmente no aportó peers. (`archiver.py`, `peers.py`, `state.py`)
- **Commit**: `265bd40`

### EC-06 — ROIC propio ausente de la tabla de peer comparison
- **Síntoma**: tabla mostraba solo peers sin la empresa analizada. Imposible comparar sin scrollear.
- **Fix**: primera fila de la tabla = subject con `← subject`. ROIC viene de `state["financials"].roic_series[0]`. (`archiver.py`)
- **Commit**: `98dc052`

### EC-07 — Tavily devolvía artículos de 5-6 años para insider ownership
- **Síntoma**: citas de Yahoo Finance 2019 para "insiders own 28%" en FSLR.
- **Causa**: `time_range` sin filtro → Tavily devuelve cualquier año.
- **Fix**: `time_range="year"` por defecto. Per-query override para CEO background (`time_range=None`) donde la historia no envejece. (`tavily.py`, `management.py`)
- **Commits**: `098c201`, `fd7c1a0`

### EC-08 — Peer question duplicada en Checkpoint 1
- **Síntoma**: el usuario veía la misma pregunta dos veces (Socratic + prompt dedicado).
- **Causa**: la peer question estaba hardcodeada en `_build_socratic_questions` aunque el CLI ya tenía un prompt dedicado para peers justo después.
- **Fix**: eliminar la peer question de las Socratic — solo genera preguntas analíticas (moat/ROIC conflict, ownership, tenure). (`checkpoint.py`)
- **Commit**: `e72baf7`

### EC-09 — growth_justification del Checkpoint 2 no llegaba al Value Expert
- **Síntoma**: el Expert ignoraba el razonamiento del usuario sobre las asunciones de growth.
- **Causa**: `growth_justification` se añadía al dict `assumptions` en CLI pero `valuation_agent` lo descartaba al parsear solo los campos de `ValuationInput`.
- **Fix**: leer `checkpoint2_user_input.get("growth_justification")` directamente en `_format_context` del Expert. (`value_expert.py`)
- **Commit**: `4536cc9`

### EC-10 — Label "5y CAGR" incorrecto cuando solo hay 3-4 periodos XBRL (FSLR)
- **Síntoma**: report decía "Historical 5y revenue CAGR: 25.8%" pero FSLR solo tenía 4 periodos → era un CAGR de 3 años.
- **Fix**: `_compute_revenue_cagr_5y` devuelve `(cagr, n_years)`. El contexto del Expert y el bloque HISTORICAL CONTEXT usan `n_years` real. (`financials.py`, `value_expert.py`, `state.py`)
- **Commit**: `13d6d46`

---

### EC-14 — Formatter $0.0B para small caps invalida §4 Financial Quality (BBW, JBSS)
- **Síntoma**: el Value Expert dice "free cash flow was $0.0B" para BBW que tiene FCF real de $39.5M. Invalida §4 y el VERDICT rationale.
- **Causa**: todas las cifras en `_format_context` se dividían por `1e9` con `.1f`, produciendo `$0.0B` para valores < $100M.
- **Fix**: helper `_fmt_usd(value)` que adapta la unidad (M/B/k) según magnitud. Aplicado en `value_expert.py` y `archiver.py`. Ver ADR-022.
- **Commit**: pendiente

### EC-15 — DCF near-term growth absurdo por FCF CAGR ruidoso (BBW: 25.6% para retailer ~7%)
- **Síntoma**: DCF upside +255% para BBW. Sensitivity table: 20.6%–30.6% near-term growth para una empresa de retail madura que crece ~7% en revenue.
- **Causa**: el near-term growth se derivaba solo del FCF CAGR. El FCF oscila ±40% año a año por working capital y timing de capex → CAGR inestable y no representativo del growth sostenible.
- **Fix**: `near_term_growth = min(FCF_CAGR, historical_cagr_5y)`. El revenue CAGR actúa como techo natural. Ver ADR-022.
- **Commit**: pendiente

### EC-16 — Sin flag cuando forward DCF y reverse DCF contradicen (BBW: spread 24pp)
- **Síntoma**: forward DCF asume 25.6% growth (→ +255% upside) pero reverse DCF implica 1.6% growth en el precio actual. El sistema no lo señalaba.
- **Causa**: no había check de coherencia entre los dos modelos.
- **Fix**: si `near_term_growth - reverse_implied_growth > 5pp` (sin user override), añadir advertencia a `dcf_reliability_note`. Ver ADR-022.
- **Commit**: `35e600c`

### EC-17 — Critic alucinaba "$0.0B FCF" cuando §4 decía "$40M" (BBW)
- **Síntoma**: contradicción interna en el report. §4 (Value Expert) decía "FCF was $40M" y el Critic decía "FCF trend shown as $0.0B for FY2023, FY2024, FY2026".
- **Causa**: cuando arreglamos EC-14, buscamos `/1e9` en `value_expert.py` y `archiver.py` pero NO en `critic.py` ni `competitive.py`. Los dos seguían enviando `$0.0B` al LLM. El Critic no alucinaba — reproducía fielmente lo que recibía.
- **Fix**: centralizar el formatter en `utils/formatting.py:fmt_usd()` como single source of truth. Eliminar duplicados en value_expert/archiver/cli y aplicar al critic + competitive. Ver ADR-023.
- **Lección meta**: cualquier patrón de formateo debe vivir en un único módulo — la búsqueda manual para encontrar duplicados es frágil.
- **Commit**: pendiente

### EC-18 — ROIC no calculable para empresas con buybacks acumulados (BBW, AutoZone, Domino's)
- **Síntoma**: el §2 Moat Analysis decía "BBW's FY2026 ROIC is not provided". `roic_series=[]` aunque la empresa tiene EBIT positivo y datos completos.
- **Causa**: la fórmula `IC = Equity + Debt - Cash` se vuelve degenerada para empresas con treasury stock acumulado por buybacks. Cuando `IC ≤ 0`, el sistema vaciaba el `roic_series` silenciosamente — excluyendo una clase entera del universo value (compounders que devuelven capital).
- **Fix**: fórmula con fallback. Si la primaria es < 10% del operating capital, usar `IC = Total Assets - Cash`. El método elegido se documenta en `MetricValue.source`. Test añadido con equity negativo simulado. Ver ADR-023.
- **Commit**: pendiente

### EC-19 — Capital returns (buybacks + dividends) ausentes del schema y del análisis (BBW)
- **Síntoma**: la tesis core de BBW ("100% del FCF devuelto al accionista vía buybacks+dividends") no aparecía en ninguna sección del report. El Value Expert dijo "compensation and capital allocation track record are not available".
- **Causa**: `FinancialsReport` no tenía campos para `buybacks` ni `dividends`. El parser de EDGAR no los extraía aunque están en el cash flow XBRL (`PaymentsForRepurchaseOfCommonStock`, `PaymentsOfDividendsCommonStock`).
- **Fix**: añadir campos al schema, parser con fallbacks de conceptos XBRL, surfacear en contexto del Value Expert con regla dura en el system prompt ("MUST report explicitly with % of FCF"). Tabla CLI también muestra las filas. Ver ADR-023.
- **Commit**: pendiente

### EC-20 — Subject row ausente del peer comparison cuando ROIC subject vacío
- **Síntoma**: tabla de peers mostraba `FNKO -17.1%, BKE 43.2%, VRA -28.2%` sin la fila de BBW. Comparison estéril sin el ancla del subject.
- **Causa**: el archiver y CLI omitían la fila del subject si `roic_series` estaba vacío (consecuencia downstream de EC-18).
- **Fix**: subject row siempre presente. Si ROIC no calculable, mostrar "N/A — IC indeterminate" explícito. Defensa en profundidad — incluso con EC-18 arreglado, el subject row es invariante de la tabla.
- **Commit**: `3fbb483`

### EC-21 — Operating Income (EBIT) no extraído para empresas con income statement simplificado (BBW)
- **Síntoma**: BBW seguía con ROIC=N/A tras EC-18 fix. Investigación en vivo reveló `operating_income=[]` aunque la empresa tiene EBIT real ~$66M.
- **Causa raíz**: BBW (y muchas small/mid caps) no reportan `OperatingIncomeLoss` como concepto XBRL. Su income statement es Revenue → COGS → GrossProfit → SG&A → IncomeBeforeTaxes → Tax → NetIncome. El parser solo intentaba `OperatingIncomeLoss` como concepto único — sin fallback.
- **Fix**: helper `_compute_operating_income(inc_df)` con cadena de fallbacks: (1) `OperatingIncomeLoss`, (2) `GrossProfit - SG&A`, (3) `Pretax - InterestExpenseNonoperatingNet`, (4) `Pretax` proxy. Verificado en vivo con BBW: ahora ROIC=40.2% sostenido (compounder).
- **Patrón**: mismo que EC-01 (OCF/capex con fallbacks múltiples) — sistema asume "perfect XBRL" y falla silenciosamente cuando varía.
- **Commit**: pendiente

### EC-22 — Margin trend solo surfaceado en compresión (no aparece para empresas con margen mejorando)
- **Síntoma**: §4 Financial Quality dice "margin trend not provided" para BBW, aunque los datos están en `financials.gross_profit + financials.revenue`. Auditoría externa preguntaba por el trend.
- **Causa**: `_format_context` solo emitía el trend cuando había compresión 3 años. Bug de "solo flageamos lo malo".
- **Fix**: emitir trend SIEMPRE que haya ≥2 años de datos. Warning de compresión solo se añade adicionalmente. Now: para BBW (52.5% → 55.8% mejorando) el trend aparece en context y §4 puede razonar sobre él.
- **Commit**: pendiente

### EC-23 — Citations con datos críticos no llegan al Value Expert
- **Síntoma**: §3 Management dice "insider sale price is not provided, so I cannot compute the required gap" — pero las citations en el appendix sí tienen los precios ($72.55, $73.21). La regla MUST de §3 (MCE-22) no se ejecuta porque la data no está en el contexto del LLM.
- **Causa**: `_format_context` añade `mgmt.insider_transaction_pattern` (string resumen sin precios) pero no incluye los excerpts de citations donde están los datos verbatim.
- **Fix**: añadir bloque "Insider transaction evidence" en context con los top 3 citation excerpts de mgmt. La regla §3 MUST ahora puede ejecutarse.
- **Lección meta**: las citations son evidencia validada — no solo para el appendix, también para alimentar al Expert. Cualquier dato verbatim crítico debe ir AL prompt, no solo al render final.
- **Commit**: pendiente

### EC-24 — Sizing inconsistente con confidence (BBW: 64% confidence, 3% sizing)
- **Síntoma**: confidence bajó (72% → 64%) pero sizing subió (2% → 3%). Lógicamente debería ser al revés — menor convicción debe implicar posición más pequeña.
- **Causa**: el LLM emite ambos campos independientemente sin enforcement. No había regla en el prompt ni clamp post-hoc.
- **Fix**: regla en `_SYSTEM_PROMPT` (`max sizing ≤ confidence × 10`) + clamp programático en `_parse_response`. Defensa en profundidad: el LLM puede ignorar la regla pero el clamp impone la disciplina.
- **Commit**: pendiente

### EC-25 — DEF 14A truncado a 12K chars omitía la sección de ownership
- **Síntoma**: `mgmt.insider_ownership_pct=null` aunque la empresa reporta ownership en su DEF 14A.
- **Causa**: `fetch_def14a_text` truncaba el proxy a 12K chars. Para Russell 2000 típico, esa zona cubre intro + governance + board pero NO la "Security Ownership of Certain Beneficial Owners and Management" table (página 30-40 de un proxy típico).
- **Fix**: extracción targeting — busca markers conocidos ("Security Ownership", "Beneficial Ownership", "Compensation Discussion") y extrae 4-5K chars alrededor de cada uno. Combina inicio + ownership + compensation en max 18K chars total. Más barato en tokens que solo subir el límite, y más robusto.
- **Commit**: pendiente

## Conocidos — pendientes de fix

### EC-11 — Foreign private issuers (ADRs no-US) fallan al intentar 10-K
- **Tickers afectados**: GRVY (Corea), ONON (Suiza), BABA (China), TSM (Taiwan), cualquier 20-F filer.
- **Síntoma**: error en supervisor/EDGAR al intentar fetchear 10-K — la empresa solo tiene 20-F.
- **Causa**: el sistema está construido para 10-K US GAAP. 20-F tiene estructura diferente e IFRS/local GAAP.
- **Status**: limitación conocida. Phase 3 añadirá soporte 20-F. Ver `docs/mce-evolution.md` MCE-13.
- **Workaround**: usar solo tickers de empresas US domésticas (o ADRs de empresas que también tengan 10-K).

### EC-12 — UAA (Under Armour) falla con `float() argument must be a string or real number, not 'NoneType'`
- **Síntoma**: `peers_node_peer_failed` al intentar fetchear UAA como peer.
- **Causa**: un campo en el XBRL de UAA devuelve `None` donde se espera un número. No investigado aún.
- **Status**: pendiente diagnóstico. Probablemente un concepto de balance sheet con valor `None` en el raw XBRL.

### EC-13 — DCF N/A cuando FCF es negativo pero EBIT es positivo (empresas en inversión)
- **Síntoma**: empresas con EBIT positivo pero FCF negativo (reinvirtiendo agresivamente) no producen DCF.
- **Causa**: el DCF requiere `base_fcf > 0`. Sin fallback a EBIT-based valuation.
- **Status**: pendiente. Workaround: el usuario puede introducir manualmente el near-term growth y el sistema usa EBIT para los múltiplos. Un EBIT-based DCF como fallback sería la solución correcta.
