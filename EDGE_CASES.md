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
- **Commit**: `35e600c`

### EC-15 — DCF near-term growth absurdo por FCF CAGR ruidoso (BBW: 25.6% para retailer ~7%)
- **Síntoma**: DCF upside +255% para BBW. Sensitivity table: 20.6%–30.6% near-term growth para una empresa de retail madura que crece ~7% en revenue.
- **Causa**: el near-term growth se derivaba solo del FCF CAGR. El FCF oscila ±40% año a año por working capital y timing de capex → CAGR inestable y no representativo del growth sostenible.
- **Fix**: `near_term_growth = min(FCF_CAGR, historical_cagr_5y)`. El revenue CAGR actúa como techo natural. Ver ADR-022.
- **Commit**: `35e600c`

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
- **Commit**: `3fbb483`

### EC-18 — ROIC no calculable para empresas con buybacks acumulados (BBW, AutoZone, Domino's)
- **Síntoma**: el §2 Moat Analysis decía "BBW's FY2026 ROIC is not provided". `roic_series=[]` aunque la empresa tiene EBIT positivo y datos completos.
- **Causa**: la fórmula `IC = Equity + Debt - Cash` se vuelve degenerada para empresas con treasury stock acumulado por buybacks. Cuando `IC ≤ 0`, el sistema vaciaba el `roic_series` silenciosamente — excluyendo una clase entera del universo value (compounders que devuelven capital).
- **Fix**: fórmula con fallback. Si la primaria es < 10% del operating capital, usar `IC = Total Assets - Cash`. El método elegido se documenta en `MetricValue.source`. Test añadido con equity negativo simulado. Ver ADR-023.
- **Commit**: `3fbb483`

### EC-19 — Capital returns (buybacks + dividends) ausentes del schema y del análisis (BBW)
- **Síntoma**: la tesis core de BBW ("100% del FCF devuelto al accionista vía buybacks+dividends") no aparecía en ninguna sección del report. El Value Expert dijo "compensation and capital allocation track record are not available".
- **Causa**: `FinancialsReport` no tenía campos para `buybacks` ni `dividends`. El parser de EDGAR no los extraía aunque están en el cash flow XBRL (`PaymentsForRepurchaseOfCommonStock`, `PaymentsOfDividendsCommonStock`).
- **Fix**: añadir campos al schema, parser con fallbacks de conceptos XBRL, surfacear en contexto del Value Expert con regla dura en el system prompt ("MUST report explicitly with % of FCF"). Tabla CLI también muestra las filas. Ver ADR-023.
- **Commit**: `3fbb483`

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
- **Commit**: `ad52e7e`

### EC-22 — Margin trend solo surfaceado en compresión (no aparece para empresas con margen mejorando)
- **Síntoma**: §4 Financial Quality dice "margin trend not provided" para BBW, aunque los datos están en `financials.gross_profit + financials.revenue`. Auditoría externa preguntaba por el trend.
- **Causa**: `_format_context` solo emitía el trend cuando había compresión 3 años. Bug de "solo flageamos lo malo".
- **Fix**: emitir trend SIEMPRE que haya ≥2 años de datos. Warning de compresión solo se añade adicionalmente. Now: para BBW (52.5% → 55.8% mejorando) el trend aparece en context y §4 puede razonar sobre él.
- **Commit**: `ad52e7e`

### EC-23 — Citations con datos críticos no llegan al Value Expert
- **Síntoma**: §3 Management dice "insider sale price is not provided, so I cannot compute the required gap" — pero las citations en el appendix sí tienen los precios ($72.55, $73.21). La regla MUST de §3 (MCE-22) no se ejecuta porque la data no está en el contexto del LLM.
- **Causa**: `_format_context` añade `mgmt.insider_transaction_pattern` (string resumen sin precios) pero no incluye los excerpts de citations donde están los datos verbatim.
- **Fix**: añadir bloque "Insider transaction evidence" en context con los top 3 citation excerpts de mgmt. La regla §3 MUST ahora puede ejecutarse.
- **Lección meta**: las citations son evidencia validada — no solo para el appendix, también para alimentar al Expert. Cualquier dato verbatim crítico debe ir AL prompt, no solo al render final.
- **Commit**: `ad52e7e`

### EC-24 — Sizing inconsistente con confidence (BBW: 64% confidence, 3% sizing)
- **Síntoma**: confidence bajó (72% → 64%) pero sizing subió (2% → 3%). Lógicamente debería ser al revés — menor convicción debe implicar posición más pequeña.
- **Causa**: el LLM emite ambos campos independientemente sin enforcement. No había regla en el prompt ni clamp post-hoc.
- **Fix**: regla en `_SYSTEM_PROMPT` (`max sizing ≤ confidence × 10`) + clamp programático en `_parse_response`. Defensa en profundidad: el LLM puede ignorar la regla pero el clamp impone la disciplina.
- **Commit**: `ad52e7e`

### EC-25 — DEF 14A truncado a 12K chars omitía la sección de ownership
- **Síntoma**: `mgmt.insider_ownership_pct=null` aunque la empresa reporta ownership en su DEF 14A.
- **Causa**: `fetch_def14a_text` truncaba el proxy a 12K chars. Para Russell 2000 típico, esa zona cubre intro + governance + board pero NO la "Security Ownership of Certain Beneficial Owners and Management" table (página 30-40 de un proxy típico).
- **Fix**: extracción targeting — busca markers conocidos ("Security Ownership", "Beneficial Ownership", "Compensation Discussion") y extrae 4-5K chars alrededor de cada uno. Combina inicio + ownership + compensation en max 18K chars total. Más barato en tokens que solo subir el límite, y más robusto.
- **Commit**: `ad52e7e`

### EC-26 — Incremental ROIC no precomputado para §5
- **Síntoma**: §5 Growth & Reinvestment decía "incremental ROIC not precomputed" — pero la información para inferirlo (ROIC trend) ya estaba en el contexto.
- **Causa**: el LLM no conectaba "ROIC trend" con "marginal ROIC proxy" automáticamente. Sin instrucción explícita, no infería la relación.
- **Fix**: en `_format_context`, surfacear interpretación explícita del trend: "RISING — marginal ROIC > average ROIC", "FALLING — marginal ROIC < average", "STABLE — ≈". Update §5 prompt rule para usar la interpretación.
- **Commit**: `b84c401`

### EC-27 — 10-K/A (amendments) leídos como 10-K originales — Item 1A/Business/MD&A invisibles
- **Síntoma**: `mgmt`/`filings` Items 1A, 1, 7 todos vacíos para BBW. §7 dice "10-K risk section not available", §1 dice "business description not provided" aunque el sistema tiene el 10-K.
- **Causa raíz** (verificada en vivo): el último filing 10-K de BBW es un **10-K/A (amendment)** que solo contiene Part IV (exhibits). El parser de edgartools devuelve secciones vacías para amendments. `filings.latest()` devolvía el amendment incompleto en lugar del 10-K original.
- **Patrón sistémico**: afecta a CUALQUIER empresa que haya enmendado su 10-K (correcciones, suplementos, restatements). Misma raíz que EC-21 (XBRL variability) y EC-25 (truncate myopia) — system asume "happy path" y falla en variantes reales.
- **Fix**: helper `_get_latest_full_10k(company)` que filtra `form == "10-K"` exactamente, excluyendo "10-K/A". Aplicado a las 4 funciones afectadas: `fetch_10k_business`, `fetch_10k_risk_factors`, `fetch_10k_mda`, `fetch_10k_item10`. Adicionalmente fallback regex sobre full text si edgartools parser falla en encontrar Item 1A.
- **Commit**: `b84c401`

### EC-28 — Moat clasificado "wide" basándose en ROIC alto sin más criterios
- **Síntoma**: BBW promovido de "narrow" a "wide" en cuarta iteración solo porque ROIC pasó de N/A a 40.2%. Análisis externo identificó la clasificación como agresiva — BBW es cash cow nicho con returns altos, no wide moat compounder.
- **Causa**: prompt del competitive_agent tenía rúbrica mínima ("wide = durable advantage 10+ years") sin criterios objetivos. LLM aplicaba heurístico mental "ROIC alto = moat fuerte = wide".
- **Patrón**: misma clase que EC-15 (FCF CAGR como growth), EC-18 (equity como IC). Signal único usado para conclusión multifactorial.
- **Fix**: rúbrica estricta multi-criterio (ADR-024) + clamp post-hoc. Wide moat requiere ROIC>15% × 4 años, capital base estable, revenue CAGR ≥4%, evidencia estructural específica. Si LLM dice "wide" pero criterios cuantitativos fallan → downgrade automático.
- **Commit**: `b84c401`

### EC-29 — Tavily ticker collision (BBW vs BBWI, MS vs MSFT)
- **Síntoma**: en sección "Extended news coverage" de BBW aparecen items sobre BBWI (Bath & Body Works) — cobertura contaminada. Items [1], [8], [9] del último report eran BBWI, no BBW.
- **Causa**: queries Tavily de MCE-19 incluían `{ticker} {company_name}` — pero tickers cortos colisionan con tickers similares en search engines (BBW/BBWI). Yahoo Finance pages para BBWI ranking alto traen ruido.
- **Fix**: drop ticker de las queries, usar SOLO `company_name`. "Build-A-Bear Workshop" es unívoco; "BBW" no. Patrón sistémico — afecta a cualquier ticker corto con prefix collision.
- **Commit**: `16555c4`

### EC-30 — 8-K filter format mismatch — 5 iteraciones con detección vacía
- **Síntoma**: BBW reportaba "No material 8-K events" en TODAS las iteraciones tras Oleada 1, aunque la empresa tiene 16 8-K filings en los últimos 12 meses (incluyendo Items 8.01 y 5.02 — directamente en el filter).
- **Causa raíz** (verificada en vivo): edgartools devuelve los items como `"Item 8.01"` (string con prefix "Item "). Nuestro filter comparaba contra `"8.01"` (sin prefix). Format mismatch silencioso → cero matches.
- **Fix**: normalizar ambos lados — strip "Item " prefix en el filter Y en los items del filing antes de comparar (case-insensitive). Verificado live: BBW pasa de 0 a 7 eventos detectados.
- **Lección**: el bug demuestra el meta-pattern de MCE-26 (auditoría sistemática post-fix): cuando expandimos el filter en MCE-20, deberíamos haber verificado contra una fixture real para ver el formato real de los items.
- **Commit**: `16555c4`

### EC-31 — DEF 14A targeted extraction capturaba TOC en lugar de la sección real
- **Síntoma**: `mgmt.insider_ownership_pct=null` aunque la tabla está en el DEF 14A (verificado: BBW tiene "Sharon John 1.8%, Directors+Execs 4.0%" en la tabla de ownership).
- **Causa**: `fetch_def14a_text` usaba `.find()` (primera ocurrencia) para localizar markers como "Security Ownership". El primer match siempre está en el Table of Contents — la sección real está más adelante. La extracción capturaba el TOC en lugar de la tabla.
- **Fix**: encontrar TODAS las ocurrencias y usar la ÚLTIMA (la del cuerpo, no del TOC). Patrón idéntico a EC-27 fallback regex para Item 1A. Adicionalmente: ventana ampliada a 8K chars y max_chars total a 24K para acomodar tablas largas (BBW ownership table tiene ~5K chars con ~17 rows).
- **Commit**: `16555c4`

### EC-32 — Incremental ROIC sin número concreto en §5
- **Síntoma**: §5 Growth decía "marginal ROIC appears positive" sin número — auditoría externa pidió valor concreto para que el reasoning sea accionable.
- **Causa**: el `_format_context` solo emitía interpretación cualitativa (RISING/FALLING), no el número del cambio.
- **Fix**: computar avg annual ΔROIC en pp/year y surfacearlo. Para BBW: "RISING — marginal ROIC > average (avg ΔROIC: +0.9pp/year over 3 years)".
- **Commit**: `16555c4`

### EC-34 — XBRL sin shares_outstanding crashea el parser (CRD.A, dual-class small caps)
- **Síntoma**: `due-diligence analyze CRD.A` lanza `TypeError: float() argument must be a string or real number, not 'NoneType'`. CRD.A (Crawford & Company class A) y otros dual-class small caps no reportan `facts.shares_outstanding` en XBRL — edgartools devuelve None.
- **Causa**: el parser hacía `float(facts.shares_outstanding)` sin null-check, y luego construía un `MetricValue(value=None)` que violaba el invariante del schema (value: float requerido).
- **Patrón sistémico**: misma clase que EC-21 (OperatingIncomeLoss), EC-25 (DEF 14A truncate), EC-27 (10-K/A) — el sistema asume "perfect XBRL coverage" y falla en variantes reales del universo (small caps, dual-class, ADRs con tagging incompleto).
- **Análisis del fix sugerido por LLM externa**: la propuesta inicial fue `MetricValue.value: Optional[float]`. **Rechazada** porque rompe el invariante del schema — `MetricValue` es el modelo central y miles de call sites pasarían a necesitar null-checks. Era resolver el síntoma (Pydantic validation error) en lugar de la causa raíz (estamos creando MetricValue cuando no deberíamos).
- **Fix correcto aplicado**:
  1. Mantener `MetricValue.value: float` requerido (invariante preservado).
  2. `FinancialsReport.shares_outstanding: Optional[MetricValue] = None` (parent opcional, hijo no).
  3. `edgar.py`: construcción condicional — solo crear MetricValue si `value is not None`, en otro caso `shares_outstanding=None`.
  4. `valuation_agent` ya tenía fallback inteligente: `shares = market_cap / price` cuando shares_outstanding es None. Verificado en línea 226 de valuation.py.
- **Lección meta**: cuando un schema invariant choca con un edge case, la pregunta correcta es "¿debería existir el objeto?", no "¿debería el campo aceptar null?". Optional en el padre es semántico ("data missing"); Optional en el value es un objeto degenerado.
- **Commit**: pendiente

### EC-33 — Headlines de news tratados como hechos validados
- **Síntoma**: §7 cita "rising receivables risk margins" del título de SeekingAlpha como si fuera un concepto financiero real. El título original probablemente era "macro headwinds and rising receivables [are a] risk [to] margins" — truncado/parsed mal.
- **Causa**: el Value Expert prompt no distingue entre "headline news" (título) y "validated claim" (corroborated by body, filings, or financial data).
- **Fix**: regla #10 nueva en `_SYSTEM_PROMPT`: news headlines son sugerentes only. Quote a headline phrase as fact ONLY if corroborated by body summary, 10-K section, or financial data. Phrases con awkward wording que parecen parsing artifacts → ignorar.
- **Commit**: `16555c4`

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
