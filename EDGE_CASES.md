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

### EC-37 — Debt extraction silent failure: edgartools curated balance_sheet omite conceptos válidos (MOS)
- **Síntoma**: report de MOS dijo "Debt is $0 and cash plus STI is $277M" — pero Mosaic tiene **$5,054M de deuda** real (verificado StockAnalysis). El sistema invirtió completamente la narrativa del balance sheet sin warning. Auditor externo (Opus 4.7) calificó como "ERROR DE DATOS CATASTRÓFICO" en su review.
- **Causa raíz** (verificada en vivo): MOS reporta su deuda en XBRL con concept `us-gaap:LongTermDebtAndCapitalLeaseObligations` (incluye capital leases, formato IFRS-style común en industriales). Este concept SÍ existe en `facts._facts` (raw fact list) pero **NO aparece** en `facts.balance_sheet().to_dataframe()` — la vista curada de edgartools lo filtra por alguna característica del filing (presentation tags / ordering / custom taxonomy extension).
- **Diferencia con bugs previos**: hasta ahora todos los fixes de extracción XBRL eran sobre **vocabulario heterogéneo** (EC-01 OCF, EC-21 EBIT — el filer usa un concept distinto). EC-37 es **un nivel arquitectónico más profundo**: el concept correcto SÍ existe en el XBRL pero la **API curada de edgartools nos lo oculta**. Nuestro `_get_point(bs_df, ...)` devolvía silently 0.
- **Fix**: nuevo helper `_get_from_facts(concept, period)` que bypasea el `bs_df` curado y query directo a `facts.get_annual_fact("us-gaap:CONCEPT")`. Helper compuesto `_extract_balance_concept(concepts, period, kind)` aplica el fallback en 2 niveles: (1) curated view primero (rápido), (2) raw facts via `get_annual_fact` con prefijo `us-gaap:`. Valida que `fiscal_year` del fact retornado coincida con target (descarta facts stale de conceptos descontinuados).
- **Aplicado a**: extracción de `long_term_debt` y `short_term_debt`. Cadena LT debt expandida con `LongTermDebtAndCapitalLeaseObligations` y `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`. Cadena ST debt expandida con `LongTermDebtAndCapitalLeaseObligationsCurrent`.
- **Verificado en vivo**: MOS pasa de "Debt $0" a **"Total Debt $5,054M"** ($4,251M LT + $803M ST). AAPL sin regresión ($90,678M LT + $12,350M ST).
- **Patrón meta**: el sistema asume que la API curada tiene todo. La realidad XBRL es heterogénea EN AMBOS NIVELES — vocabulario del concept (EC-01/21) Y disponibilidad en views curadas (EC-37). Cualquier extracción crítica debe tener fallback al raw facts.
- **Commit**: `11985d3`

### EC-39 — Insider buying clasificado sin distinguir magnitud (MOS audit: $17K token = "neutral")
- **Síntoma**: §3 calificó una compra de director de $17K como "neutral" / "modest confidence" (MOS audit), aunque es claramente un token purchase de cumplimiento. El sistema trataba CUALQUIER compra como "alignment signal" sin distinguir magnitud.
- **Patrón sistémico**: cualquier análisis con insider activity. Una compra de $17K vs $5M tiene implicaciones completamente distintas. Adicionalmente: la AUSENCIA de open-market buying por CEO/CFO durante un drawdown >30% es una señal por sí misma (insiders chose not to add at depressed prices).
- **Fix**: regla #11 en `_SYSTEM_PROMPT` con rubric de 4 niveles:
  - Token purchase (<$50K total): cosmetic compliance, NOT conviction
  - Modest ($50K-$250K): mild confidence signal
  - Material (>$250K): genuine conviction
  - ABSENCE during drawdown >30%: itself a negative signal — state explicitly
- Schema §3 description actualizada para forzar la clasificación.
- **Commit**: `911f995`

### EC-40 — Tariff/regulatory risk analizado a nivel empresa, no por segmento
- **Síntoma**: §7 de MOS dijo tariff es "direct margin and competitiveness risk" sin diferenciar segmentos. Realidad: el segmento potash absorbió la tariff vía pricing (+$275M EBITDA YoY), el segmento phosphate fue machacado por input costs (-$280M en raw materials sulphur/ammonia). MISMO risk con SIGNOS opuestos por segmento.
- **Patrón sistémico**: cualquier multi-segment company. Examples: GOOGL (search vs cloud vs YouTube), AMZN (retail vs AWS), NVDA (datacenter vs gaming). Generic "tariff risk" o "regulatory risk" sin segmentación pierde la dirección causal real del problema.
- **Fix**: regla #12 en `_SYSTEM_PROMPT` y schema §7 description. Si business_description menciona múltiples segmentos, MUST analizar named risks per segmento, no a nivel empresa. Cuantificar cuando data lo permite.
- **Commit**: `911f995`

### EC-41 — Quarterly news data mencionada como factoide pero no integrada en thesis
- **Síntoma**: report MOS §7 mencionó "Q1 2026 net loss $258M" como dato aislado. NO actualizó §4 Financial Quality (sigue anclado en FY2025) ni §5 Growth conclusions. Tampoco integró el guidance withdrawal de phosphate como red flag con peso en el verdict.
- **Patrón sistémico**: cualquier análisis run mid-year donde hay news con quarterly data más reciente que el 10-K. Es el caso normal — el sistema queda anclado en data anual cuando news tiene info más fresca.
- **Fix**: regla #13 en `_SYSTEM_PROMPT` y schema §4/§5/§7 descriptions. Si news menciona Q1/Q2/Q3 metrics post-10-K (loss/EBIT/guidance/margin QoQ), MUST integrar en §4 y §5, no solo §7. Guidance withdrawals/cuts son red flags materiales con peso explícito en verdict.
- **Commit**: `911f995`

### EC-42 — Peer set sin validación de SIC (MOS audit: CF/FMC/CTVA peers irrelevantes)
- **Síntoma**: MOS auditor identificó que CF (nitrógeno fertilizante, business distinto), FMC (pesticidas, aún más distinto), CTVA (seed + crop protection) **no son apples-to-apples peers** para Mosaic (phosphate + potash). La conclusión del moat analysis se basaba en peer set inadecuado.
- **Patrón sistémico**: peers son user input. El sistema asumía que el usuario elige bien. Pero el usuario puede elegir mal, especialmente para empresas en sub-sectores específicos (fertilizers tiene 4-5 sub-categorías muy distintas).
- **Fix**: validación SIC en `peers_node`. Para cada peer, fetch `company.sic` via edgartools. Comparar prefijo 2 dígitos vs el SIC del subject. Si difiere → añadir warning a `state["peer_sic_mismatches"]`. Value Expert recibe estos warnings en context y debe reflejarlos en §2 Moat Analysis (descontando peso del peer mismatched).
- **No bloquea**: el sistema sigue procesando los peers. Solo informa al Expert para que ajuste su análisis.
- **2 tests añadidos**: same-prefix SIC (no warning) y different-industry-group (warning surface a state).
- **Commit**: `911f995`

### EC-38 — ROIC trend truncado a 3 años oculta ciclicidad (MOS picos cíclicos invisibles)
- **Síntoma**: report MOS mostraba "ROIC: FY2023 5.7%, FY2024 2.6%, FY2025 3.5%" en §2 y §5 — 3 puntos. Pero las citations tenían 4 (FY2022: 20.3%, FY2023: 5.7%, FY2024: 2.6%, FY2025: 3.5%). El cálculo del avg ΔROIC SÍ usaba la serie completa (-5.6pp/year sobre 3 años intervals = consistente con (3.5 - 20.3) / 3), pero el DISPLAY truncaba ocultando el pico cíclico FY2022 20.3%.
- **Impacto en thesis**: para empresas cíclicas como MOS (fertilizantes — pico post-guerra Ucrania 2022), el patrón "pico extremo → regresión brutal a la media" es la lectura correcta. Mostrar solo 3 años del periodo de regresión oculta el contexto cíclico y produce conclusión incorrecta (sistema dice "ROIC declining slowly" cuando la realidad es "colapso 97% desde pico").
- **Causa**: slice `[:3]` hardcoded en 5 sitios (`value_expert.py`, `archiver.py`, `cli.py`, `critic.py`, peer comparison block en value_expert). Decisión legacy de Phase 1 cuando los reports eran más cortos.
- **Fix**: cambiar a `[:5]` en todos los display sites para subject ROIC. La cadena `[:4]` en `competitive.py` (criterio de wide moat) se mantiene — es threshold semántico, no display. La cadena `[:3]` en `peers.py` para trail de peers también se mantiene — los peers no necesitan tanta historia como el subject.
- **Commit**: `11985d3`
- **Síntoma**: `due-diligence analyze NMM` (Navios Maritime Partners, Marshall Islands shipping LP) crashea con `AttributeError: 'NoneType' object has no attribute 'period_of_report'` envuelto en `ValueError: NMM: could not resolve ticker in EDGAR`. Mensaje engañoso — el ticker SÍ está en EDGAR (CIK 1415921 obtenido), pero NMM no tiene 10-K (22 filings 20-F en su lugar).
- **Causa raíz**: `supervisor_node` llamaba `company.get_filings(form="10-K").latest()` y asumía que devolvía siempre un filing. Para foreign filers, devuelve `None` → `.period_of_report` crashea. El except genérico oculta la categoría real del problema.
- **Patrón sistémico**: misma clase que EC-11 (foreign ADRs no soportados — ya conocido) y EC-35 (financial firms). Tres categorías de empresas fuera del scope:
  1. **Financieras** (SIC 6000-6799) — detectado en `EdgarAdapter.fetch_raw` (EC-35)
  2. **Foreign filers** (Form 20-F) — detectado en `supervisor_node` (EC-36, este)
  3. **Sin annual report** (private, pre-SPAC, delisted) — detectado en `supervisor_node`
- **Fix**: refactorizar `supervisor_node` con detección categorizada:
  1. Step 1: ticker resolution (`Company(ticker)` + `.cik`) — fail con `ValueError("ticker not found")` si no existe
  2. Step 2: 10-K availability (`get_filings("10-K").latest()`)
     - Si existe → continuar con flujo normal
     - Si None → Step 3: check 20-F. Si existe → `DataUnavailableError("foreign private issuer")` con mensaje educativo (industry, última 20-F fecha, referencia a Phase 3/MCE-13)
     - Si tampoco hay 20-F → `DataUnavailableError("private/SPAC/delisted")` con mensaje específico
- **Beneficios**:
  - Mensaje educativo en lugar de stack trace críptico — el usuario entiende QUÉ pasa y cuándo se soportará
  - Tres categorías distinguidas — antes todas caían en el mismo "could not resolve ticker"
  - Consistente con el patrón de EC-35 (detect-and-fail-loud por categoría arquitectónica)
- **Para NMM específicamente**: el mensaje ahora dice "Navios Maritime Partners L.P. is a foreign private issuer (industry: Deep Sea Foreign Transportation of Freight). It files Form 20-F... Last 20-F filed: 2026-03-12 for FY 2025-12-31."
- **Commit**: `87c0170`

### EC-35 — Empresas financieras (SIC 6000-6799) producen errors crípticos en extracción de revenue (AGM)
- **Síntoma**: `due-diligence analyze AGM` (Federal Agricultural Mortgage / Farmer Mac) lanza `DataUnavailableError: AGM: revenue concept not found in income statement`. Mensaje confuso porque AGM SÍ reporta income — solo que no como "Revenue".
- **Causa raíz**: AGM es una empresa **financiera** (SIC 6111 — Federal & Federally-Sponsored Credit Agencies). Las financieras no usan los conceptos XBRL estándar `RevenueFromContractWithCustomer` o `Revenues`. Sus equivalentes son: `InterestIncomeOperating`, `InterestExpense`, `InterestIncomeExpenseNet` (Net Interest Income), `InvestmentIncomeInterest`. La analogía de "revenue" para banks/insurers/mortgage cos es Net Interest Income — un concepto **estructuralmente distinto**.
- **Análisis arquitectónico**: añadir fallbacks que mapean NII a "revenue" sería un **error metodológico**, no una mejora. Las financieras requieren un framework completo distinto:
  - DCF / EV/EBIT NO aplica → usan P/Tangible Book, P/E sobre normalized earnings, dividend discount model
  - ROIC NO aplica → usan ROE, ROA, NIM (Net Interest Margin)
  - Capex casi inexistente; "inventario" son loans/deposits
  - "Operating margin" no tiene sentido → eficiencia ratio (non-int expense / revenue)
- **Decisión** (consistente con ADR-005 "fail loud"): detectar empresas financieras por SIC code (6000-6799) y rechazar con mensaje educativo. Producir output con framework no-financiero sería misleading — números con apariencia de validez pero indefendibles.
- **Fix**: en `EdgarAdapter.fetch_raw`, check SIC code via `company.sic` antes de extracción. Si está en rango 6000-6799, raise `DataUnavailableError` con mensaje claro explicando (a) que es empresa financiera, (b) qué framework usaría, (c) que está diferido a Phase 3+.
- **Patrón meta**: misma clase de problema que EC-11 (foreign 20-F filers) — empresa fuera del scope arquitectónico del sistema. Detect-and-fail-loud es más honest que producir output cuestionable.
- **Phase 3+ trabajo necesario**: ver MCE-27 en `mce-evolution.md` para el roadmap de soporte de financials (separate ROE-based valuation agent, NIM/efficiency metrics, P/TBV multiples).
- **Commit**: `7d3a17d`

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
- **Commit**: `c8fbaaf`

### EC-33 — Headlines de news tratados como hechos validados
- **Síntoma**: §7 cita "rising receivables risk margins" del título de SeekingAlpha como si fuera un concepto financiero real. El título original probablemente era "macro headwinds and rising receivables [are a] risk [to] margins" — truncado/parsed mal.
- **Causa**: el Value Expert prompt no distingue entre "headline news" (título) y "validated claim" (corroborated by body, filings, or financial data).
- **Fix**: regla #10 nueva en `_SYSTEM_PROMPT`: news headlines son sugerentes only. Quote a headline phrase as fact ONLY if corroborated by body summary, 10-K section, or financial data. Phrases con awkward wording que parecen parsing artifacts → ignorar.
- **Commit**: `16555c4`

### EC-43 — "Report saved" nunca aparecía en CLI (bug pre-existente)
- **Síntoma**: la línea `Report saved: data/reports/...` nunca se mostraba tras completar un análisis. Performance metrics tampoco se appendeaban al markdown.
- **Causa**: el CLI buscaba `{ticker}-{date}.md` (ej: `ANET-2026-05-15.md`) pero el archiver guardaba como `{date}-{ticker}-{verdict}.md` (ej: `2026-05-15-ANET-HOLD.md`). `report_file.exists()` siempre False.
- **Fix**: alinear naming en `cli.py` con el formato del archiver: `{date}-{ticker}-{verdict}.md`. Extraer verdict del `state["synthesis"]`.
- **Commit**: pendiente

### EC-44 — 10-K stale sin datos quarterly disponibles (MRCY audit)
- **Síntoma**: Value Expert analizaba MRCY con datos del 10-K FY2025 (as_of junio 2025) cuando ya había Q1 2026 con margin compression -140bps QoQ. El análisis ignoraba deterioro trimestral visible.
- **Causa**: sistema solo usaba datos anuales del 10-K. Sin integración de datos trimestrales (8-K press releases ni 10-Q XBRL).
- **Fix**: dual-source quarterly data (ADR-025): (1) 8-K Item 2.02 earnings press release en `market_agent`, (2) 10-Q XBRL snapshot en `financials_node` cuando 10-K >90 días stale. Staleness warning >180 días en Value Expert context.
- **Commit**: pendiente

### EC-45 — Thesis-breaker contradice los propios datos del análisis (MRCY)
- **Síntoma**: Value Expert declaró "thesis breaks if gross margin falls below 30%" mientras §4 ya reportaba gross margin al 28%. Contradicción lógica en el report.
- **Causa**: LLM no cross-checkea su thesis-breaker contra sus propias secciones previas.
- **Fix**: Rule #15 (prompt self-check) + `_thesis_break_self_check()` post-processing heurístico que detecta contradicciones keyword-based y appends caveat warning. ADR-026.
- **Commit**: pendiente

### EC-46 — Sin warning cuando 10-K data es stale (>180 días)
- **Síntoma**: Value Expert analizaba con data de 6+ meses sin indicación de que los datos pueden estar desactualizados.
- **Fix**: warning explícito inyectado al inicio de la sección FINANCIALS en `_format_context` cuando `as_of_date` del 10-K es >180 días.
- **Commit**: pendiente

### EC-47 — CAGR histórico sin qualifier para periodos atípicos
- **Síntoma**: CAGR de 3 años (incluye COVID recovery) presentado al LLM como si fuera representativo del growth sostenible. Anchor engañoso para DCF.
- **Fix**: qualifiers en `_format_context`: short span (<4 años), extreme growth (>40%), negative CAGR. Cada uno con warning específico y guidance para el LLM.
- **Commit**: pendiente

### EC-48 — Finnhub transcripts vacíos sin fallback (free tier)
- **Síntoma**: management_agent no recibía context de earnings calls cuando Finnhub free tier devuelve []. Pérdida de señal cualitativa (guidance, tone, management commentary).
- **Fix**: fallback Tavily en `management_agent` — busca summaries/highlights de earnings calls via web cuando Finnhub devuelve []. No reemplaza transcript verbatim pero da contexto.
- **Commit**: pendiente

### EC-49 — Value Expert JSON truncado mid-response con finish_reason="stop" (ANET)
- **Síntoma**: `value_expert_parse_failed` — JSON incompleto (1716 tokens de 8192 max). Report vacío: confidence=0%, sizing=0%, sin secciones. `finish_reason` era "stop", no "length".
- **Causa**: `gpt-5.4-mini` sin `response_format={"type":"json_object"}` puede interpretar una `}` intermedia dentro del JSON como señal de stop. Diferente de EC-03 (que era por max_tokens insuficiente).
- **Fix**: (1) `response_format={"type":"json_object"}` en la llamada al value_expert — fuerza JSON válido completo, (2) retry automático si parse falla (defense-in-depth), (3) nuevo param `response_format` en `LLMClient.complete()`.
- **Commit**: pendiente

### EC-50 — CEO selling tratado como señal bearish cuando es 10b5-1 plan (ANET)
- **Síntoma**: Value Expert usó "insider distribution pattern" de CEO Ullal como factor contra el BUY. Realidad: las ventas eran bajo plan 10b5-1 adoptado meses antes, para trusts de familiares. No informativas sobre sentimiento actual.
- **Patrón sistémico**: cualquier empresa con CEO/CFO que tenga 10b5-1 (la mayoría de large caps). Sin detección, el LLM trata toda venta como signal bearish.
- **Fix dual-layer**:
  1. **Data layer**: `_detect_10b5_1_plan()` busca "10b5-1" en footnotes del Form 4 + texto del filing. Marca transacciones como `is_10b5_1_plan: True`.
  2. **Format layer**: `format_form4_for_prompt()` separa en 3 categorías: discretionary (informative), 10b5-1 planned (NOT informative), institutional (NOT insider sentiment). Con nota explícita para el LLM.
  3. **Prompt layer**: Rule #17 en Value Expert system prompt prohíbe usar ventas 10b5-1 como señal bearish.
- **6 tests añadidos**: detección footnotes, filing text, no-plan, missing attrs, format separation, all-planned.
- **Commit**: pendiente

### EC-51 — Press release quarterly vacío (Exhibit 99.1 no extraído)
- **Síntoma**: Value Expert dice "quarterly press-release body is not included here" para ANET Q1 FY2026 pese a que el 8-K Item 2.02 sí existe. El report pierde revenue +35% YoY, guidance raise, gross margin QoQ compression.
- **Causa**: `_extract_8k_item_text(obj, ["2.02"])` solo extrae texto inline del Item 2.02. Pero ~90% de 8-K earnings usan Exhibit 99.1 como vehículo — el Item 2.02 inline es solo un header de referencia ("On May 5, Arista issued a press release...").
- **Fix**: priority chain en `fetch_earnings_press_release()`:
  1. `filing.attachments.exhibits` → buscar `EX-99.*` → `exhibit.text()` (press release real)
  2. Fallback: `_extract_8k_item_text(obj, ["2.02"])` (inline text)
  3. Fallback: `obj.body` (body completo)
- **Impacto**: desbloquea la feature EC-44 (quarterly data integration) para ~90% de empresas que antes recibían solo el header vacío.
- **Commit**: pendiente

### Fix menor — DCF "contradiction" renombrado a "spread note"
- **Hallazgo**: el warning decía "DCF contradiction" lo cual implica un error. Realidad: es la divergencia esperada entre forward DCF (analyst assumptions) y reverse DCF (market-implied growth). No es una contradicción, es información.
- **Fix**: rename a "DCF spread note" con framing correcto. Actualizado en `valuation.py`, `critic.py`, y tests.
- **Commit**: pendiente

## Conocidos — pendientes de fix

- **Detector cíclico — falsos positivos en empresas que pasan de pérdidas a beneficios (EC-92/ADR-034)**: al congelar el dataset de H2-1, PATH (software) y PLUG salen `is_cyclical=True`: con margen mediano ≤ 0 la regla acepta cualquier pico ≥ 8 pp, y un giro a beneficios lo es. Efecto: g1 = crecimiento terminal para una empresa en crecimiento. Calibración original con 7 empresas sin este perfil. Revisar con el harness (no cambiar el dataset congelado).
- **BBW con +299 % de upside de DCF en el dataset H2-1**: revisar si es real o un artefacto de la base FCFF/crecimiento.
- ~~**Filings sin citas verificadas en 6/13 casos del dataset H2-1**~~ — RESUELTO 2026-10-10 cambiando los especialistas a gpt-6-luna (ADR-039): 13/13 casos con evidencia en los tres especialistas.

Hallazgos de la primera baseline del harness (H2-1, `tests/evals/baselines/full.run.json`, gpt-5.4-mini, 14 casos × 5):
- **[RESUELTO en parte 2026-10-11, ADR-040 + ADR-041]** **HOLD por defecto (síntoma de H1 confirmado con datos)**: 14/14 casos HOLD por mayoría en full-1 (13/14 en full-2). AZO con los 3 métodos calculables "expensive" (DCF −66 %) → HOLD; PLUG (ROIC −99,9 %, sin moat, FCF −647 M) → HOLD 4 de 5. HOLD absorbe "buena empresa cara" y "empresa rota". Fix previsto: H2-2 (taxonomía BUY/WATCHLIST/PASS/SELL), validado con la puerta.
- **[RESUELTO 2026-10-10 con gpt-6.1-sol, ADR-040]** **Regla 24 incumplida**: con 3 hipótesis del inversor, §8 las evalúa con palabras en minúscula ("partially supported", "unsupported", "untestable") en vez de las etiquetas SUPPORTED/CONTRADICTED/UNTESTABLE en 5/5 repeticiones; el validador del código añade el aviso (repairs ≈ 1). Fix: cambio de prompt → primer uso real de la puerta.
- **[RESUELTO 2026-10-10 con gpt-6.1-sol: 0,07–0,19 cifras/informe]** **El Expert calcula porcentajes propios (invariante 1)**: ~1,5 cifras sin respaldo por informe, casi todas distancias calculadas por el LLM (precio de venta de insiders frente al precio actual: "15.5% below current price"; margen bruto trimestral). Aritmética correcta en la muestra, pero son números generados por el LLM. Fix candidato: dar esas distancias calculadas en el contexto (Form 4 vs precio). 3/114 son falsos positivos de signo del check ("48.5% below" frente a "−48.5%"), aceptados.
- **Valoración con sesgo sistemático a la baja (2026-10-11, `docs/audits/2026-10-11-valuation-diagnostic.md`)**: en 33 empresas, la mediana DCF/precio de las 14 de calidad es 0,41 (EPV 0,35); DCF y EPV de la misma empresa discrepan hasta 3–4×. El veredicto H2 hereda estos números. Causa raíz: un método y un prompt para todo tipo de empresa → plan "valoración por perfiles".
- **Crecimiento del DCF = CAGR de FCF 5 años**: extrapola caídas puntuales (KO −9 %, ORLY −8 %, XOM −10 %, NKE −15 %) y ciclos de capex (MSFT 3,6 %). Solo se acota por arriba (CAGR de ingresos), nunca por abajo.
- **[RESUELTO 2026-10-11, EC-101]** **Acciones multiclase en la valoración**: V 0,469 B acciones (≈1,9 B reales), MA 0,123 B (0,906 diluidas) → valor por acción inflado 3–7×. Probable también en GOOGL. Misma familia que EC-93.
- **[RESUELTO 2026-10-11, EC-103]** **MA sin beneficio neto**: `annual.net_income` vacío para MA (la cadena es solo `NetIncomeLoss`; MA debe de usar otro concepto). Afecta a ROE, payout y a cualquier cálculo que use el beneficio neto. Detectado al verificar EC-101; pendiente de F0.
- **[RESUELTO 2026-10-11, EC-100]** **Periodo fiscal de minoristas**: HD y TGT fallan con `DataUnavailableError: latest 10-K period … (period misalignment)` (cierre fiscal finales de enero / principios de febrero).
- **[RESUELTO 2026-10-11, EC-102]** **Caja de TGT infravalorada (0,25 B$ frente a 5,49 B$)**: TGT presenta la caja del balance como `CashCashEquivalentsAndShortTermInvestments`, que no está en `CASH_CONCEPTS`; `resolve_cash` cae a `facts:Cash`, una partida más estrecha. Afecta a cualquier empresa que presente caja + inversiones a corto en una sola línea. Detectado al verificar EC-100; pendiente (ojo a la doble resta de STI, EC-81).
- **Beta = 1 para todas las empresas**: WACC ~9 % casi uniforme; penaliza a los negocios defensivos.
- **Moat demasiado severo (LLM)**: 12/13 casos del dataset "sin moat" → nunca "compounder" (ADR-041) → margen 50 % y ancla mín(DCF, EPV) → se pierden oportunidades en negocios buenos. Pasar a evidencia cuantitativa (ROIC > coste de capital 7–10 años) + explicación del LLM.
- **Log con caracteres no ASCII rompe un análisis si stdout no es UTF-8 (Windows, salida redirigida)**: `valuation_math.py:255` ("weight ≈ 0") lanzó `UnicodeEncodeError` en ADP al redirigir el diagnóstico a un fichero (cp1252). En consola no pasa (Python usa UTF-8). Un log nunca debería abortar el cálculo: configurar stdout con `errors="replace"` o UTF-8 en el arranque. Menor; detectado en F0.
- **Tests con fechas fijas frente a ventanas relativas a hoy**: `test_fetch_8k_events_filters_by_item` caducó el 2026-10-11 (fixture "2025-10-15" frente a "hoy − 360 días"); arreglado con fechas relativas en `tests/test_edgar_events.py`. Otros ficheros de tests tienen fechas fijas; auditar si dependen de `date.today()`.
- **BBW inestable**: BUY 2/5 en full-1, 0/5 en full-2, 1/3 en quick (3 de 4 métodos "cheap", ROIC FALLING, insiders vendiendo). Caso de referencia de estabilidad para H2-2/H2-3.

- **Proxy — recall (EC-93)**: CRM (% sin signo, solo en la cabecera) y DVN (nota al pie pegada sin paréntesis, "5,284,433 7") devuelven None — sin cifra, nunca una cifra falsa. Tablas publicadas como imagen: None.
- **Citas de management más allá del %**: AAPL 0/6 tenía la tabla completa en el texto; la causa del rechazo del resto de citas (paráfrasis) queda para la tarea B (control de evidencia).

- **Cíclicas — ventas en pico (EC-92, parcial)**: la normalización usa el margen mid-cycle × ventas ACTUALES. En materias primas las ventas también suben en el pico (precio); normalizarlas exige precios mid-cycle que no tenemos. Tampoco se usan datos TTM: CALM ya pierde dinero en el Q1 FY2027 y la valoración no lo incorpora (H3).

### Pendientes tras H1 (2026-10)

- **Crecimiento de stage 1 frágil**: derivado del CAGR histórico del FCF. AZO −4,6 % por capex en expansión vs revenue +8,4 %; BBW 15,7 % por una ventana que arranca en el valle COVID. Solución prevista: base rates (H3-4).
- **Revenue sin `merge_annual`**: `annual_series` de revenue aún no fusiona la cadena año a año; un cambio de concepto antes de 2018 deja años antiguos sin dato (la ventana de 5 años no se ve afectada hoy).
- **Validación semántica claim↔excerpt** (H2-5, MCE-31): la validación de citas prueba que el texto existe, no que sostenga el claim.
- **Déficit de pensiones fuera del bridge** (H3); LTI puede incluir participaciones operativas; leases operativos no capitalizados (H3).
- **Prima de tamaño OFF** hasta tener β por industria (H3-5).
- **Tabla de spreads del rating sintético** pendiente de verificar contra Damodaran `ratings.htm`; Kd usa deuda contable como proxy del valor de mercado.
- **`cash_series` vs `cash_and_equivalents`**: `cash_series` se define como caja + STI mientras `cash_and_equivalents` es solo caja — revisar consistencia.
- **Restatements** en `backfill_gaps`/`EdgarFactsLookup` (toman el filing más reciente); `operating_income` trimestral solo con `OperatingIncomeLoss`.
- **Inputs FCFF ausentes en fixtures legacy**: ya regenerados en T14 para los 4 arquetipos; cualquier fixture antiguo restante cae a OCF − capex con nota de reliability.

### EC-11 — Foreign private issuers (ADRs no-US, 20-F filers)
- **Tickers afectados**: GRVY (Corea), ONON (Suiza), BABA (China), TSM (Taiwan), NMM (Marshall Islands), cualquier 20-F filer.
- **Síntoma original**: crash críptico (`AttributeError: NoneType has no attribute period_of_report`) envuelto en mensaje engañoso "could not resolve ticker".
- **Status — detection mejorada en EC-36 (commit pendiente)**: el supervisor ahora distingue 3 categorías y emite mensaje educativo cuando detecta 20-F. **No analiza la empresa todavía** — soporte arquitectónico de 20-F sigue diferido a Phase 3 (MCE-13). Pero el error ya no es críptico — el usuario sabe exactamente qué pasa y cuándo se soportará.
- **Workaround actual**: usar solo tickers de empresas US domésticas (o ADRs de empresas que también tengan 10-K).
- **Phase 3 trabajo necesario**: ver MCE-13 — adapter para 20-F IFRS, schema con currency awareness, partnership distributions (K-1).

### EC-12 — UAA (Under Armour) falla con `float() argument must be a string or real number, not 'NoneType'`
- **Síntoma**: `peers_node_peer_failed` al intentar fetchear UAA como peer.
- **Causa**: un campo en el XBRL de UAA devuelve `None` donde se espera un número. No investigado aún.
- **Status**: pendiente diagnóstico. Probablemente un concepto de balance sheet con valor `None` en el raw XBRL.

### EC-52 — CEO role confusion: management agent identifica como CEO a un ex-CEO convertido en Director (CXDO)
- **Síntoma**: §3 reporta "The CEO is a founder-CEO with 15 years of tenure... the CEO sold 25,000 shares" para Mihaylo (Director con 36.76% ownership). CEO real es Jeff Korn desde marzo 2023.
- **Causa raíz (doble)**:
  1. El LLM anchó en DEF 14A "Mr. Mihaylo was appointed CEO in 2008" (historical language) e ignoró el label "Director" del Form 4. El system prompt no tenía regla explícita sobre cuál fuente es autoritativa para el rol *actual*.
  2. El management agent no recibía eventos 5.02 (leadership transitions). El nombramiento de Korn como CEO estaba en un 8-K Item 5.02 pero ese filing solo llegaba al value_expert como resumen de 200 chars — demasiado tarde y demasiado truncado.
- **Efecto colateral**: el CEO real (Korn, $253K vendidos) tenía señal más relevante que fue ignorada; la venta de Mihaylo (0.22% de su posición = ruido estadístico) fue amplificada.
- **Fix**:
  1. Regla explícita en `_SYSTEM_PROMPT` de `management.py`: Form 4 position label es autoritativo para el rol actual; DEF 14A historical appointments pueden describir roles pasados.
  2. `management_agent()` ahora fetchea `EdgarEventsAdapter.fetch_8k_events(ticker, items=["5.02"], months=12)` y lo inyecta como SOURCE 6 en el prompt. Es la fuente primaria SEC para CEO/CFO/COO transitions recientes.
- **Aplicabilidad**: cualquier empresa donde ha habido un cambio de CEO en los últimos ~3 años. Especialmente relevante cuando el ex-CEO sigue siendo major shareholder (common en founder-led companies que contratan un operador externo).
- **Commit**: pendiente

### EC-53 — M&A post-10-K ignorado por el sistema: adquisición de $35M no analizada en §4/§5/§6 (CXDO)
- **Síntoma**: CXDO adquirió Estech Systems (ESI) por $35M en marzo 2026 — la mayor adquisición de su historia. El informe no menciona ESI en ninguna sección, presenta deuda $228K como "conservatively positioned" (ignorando que se consumió ~$27.3M de cash post-10-K), y el Q1 +29% revenue se interpreta como crecimiento orgánico (era mayormente inorgánico — ESI aportó ~$2.5M de 1 mes de revenue).
- **Causa raíz (triple)**:
  1. El Item 1.01 del 8-K de la adquisición llegaba al value_expert truncado a 200 chars — insuficiente para conocer términos del deal.
  2. No había ninguna Rule en el value_expert system prompt para Item 1.01 M&A (las Rules existentes cubrían Item 4.02 restatements y Item 2.02 earnings). El LLM no tenía instrucción de "cuando hay M&A post-10-K, el balance sheet es stale".
  3. El sistema tiene mecanismo de staleness para earnings (>90 días desde 10-K) pero no para cambios estructurales de capital post-10-K.
- **Fix**:
  1. En `value_expert.py` `_format_context()`: detección de Item 1.01 con M&A keywords (`"purchase agreement"`, `"acquisition"`, `"merger agreement"`, etc.). Cuando detectado: bloque `## POST-10-K ACQUISITION DETECTED` con texto completo (800 chars) + ⚠️ balance sheet staleness warning. Patrón auto-flag del proyecto.
  2. Rule #19 (numeración corregida en H1; la 18 es PEER RELATIVE VALUATION) en `_SYSTEM_PROMPT` del value_expert: cuando el bloque está presente, §4 MUST flag balance sheet stale, §5 MUST distinguir orgánico/inorgánico, §6 MUST cavetar EV multiples, §9 MUST nominar pending 10-Q como evento resolutor.
- **Aplicabilidad sistemática**: cualquier empresa que haya hecho una adquisición material entre el 10-K y la fecha del análisis. Especialmente crítico para small/mid caps con M&A frecuente donde el 10-K puede tener 3-6 meses de antigüedad.
- **Commit**: pendiente

### EC-13 — DCF N/A cuando FCF es negativo pero EBIT es positivo (empresas en inversión) — RESUELTO
- **Síntoma original**: empresas con EBIT positivo pero FCF negativo (cyclical trough o heavy reinvestment) producían DCF N/A y reverse DCF "not provided". Reactivado por MOS FY2025 (FCF -$535M, EBIT $822M) — el report decía explícitamente "I cannot state the exact reverse-DCF implied growth number because it was not provided".
- **Causa**: `valuation_agent` requería `base_fcf > 0` para todo el flujo (DCF, reverse DCF, sensitivity table). Sin fallback.
- **Fix**: nuevo helper `_normalized_fcf_proxy(ebit, net_income)` que computa NOPAT = EBIT × (1 - effective_tax_rate). Tax rate derivado del gap EBIT→NI cuando ambos disponibles (clamp 10-40%), default 25% (US corporate post-TCJA mediana).
- **Estrategia condicional en `valuation_agent`**:
  - Si `base_fcf > 0` → usar FCF normal (comportamiento previo)
  - Si `base_fcf <= 0` Y `ebit > 0` → usar NOPAT proxy. `dcf_base_source = "NOPAT proxy (EBIT × (1-tax))"`
  - Si ninguno aplica → DCF=N/A (caso financiero o sin EBIT)
- **DCF, reverse DCF, sensitivity table** todos usan el mismo `dcf_base` resultante. La `dcf_reliability_note` documenta cuándo se usa NOPAT proxy:
  > "⚠️ DCF computed on NORMALIZED NOPAT ($617M = EBIT × (1-tax)) because reported FCF was negative (-$535M in FY2025). This is appropriate for cyclical troughs or heavy reinvestment but ignores working capital and maintenance vs growth capex split — treat as directional, not point estimate."
- **Limitación documentada**: sobreestima FCF sostenible para cyclicals en trough (cuando maintenance capex es alto). El note lo marca como "directional only" para que el lector lo trate apropiadamente.
- **Commit**: `11985d3`

### EC-54 — Near-term growth invisible: el parámetro más sensible del DCF no aparecía en ninguna capa del sistema

- **Síntoma**: el report .md mostraba `WACC=9%, terminal=2.5%, 10y` pero omitía el near_term_growth. El CLI preguntaba `[auto from history]` sin mostrar qué produciría "auto". La `dcf_reliability_note` solo se construía en condiciones de error (growth capeado, spread DCF). El Value Expert recibía el DCF calculado sin saber con qué tasa de crecimiento ni de dónde vino.
- **Causa raíz**: `near_term_growth` era variable local en `valuation_agent()`, nunca persistida en `ValuationReport`. La nota de fiabilidad era condicional — solo en error conditions — lo que la hacía invisible para el caso nominal (empresa con FCF histórico normal, sin cap, sin spread).
- **Impacto**: para empresas con 7% fallback (sin historial de FCF positivo: turnarounds, startups, heavy reinvestment), el DCF se presentaba sin ninguna señal de que la tasa de crecimiento no tenía base empírica. Para empresas con revenue_cagr_fallback, el proxy era invisible.
- **Fix — 5 capas independientes**:
  1. **Schema**: 2 nuevos campos en `ValuationReport` — `near_term_growth_used_pct: Optional[float]` y `near_term_growth_source: Optional[str]` con 5 posibles valores (`user_override`, `fcf_cagr_Ny`, `fcf_cagr_Ny_rev_capped`, `revenue_cagr_fallback`, `default_7pct`).
  2. **valuation.py**: extraído `_compute_near_term_growth()` como función pura (testeable + reutilizable desde CLI). `dcf_reliability_note` siempre construida con growth context como primera línea; nivel de alerta varía por fuente.
  3. **cli.py**: pre-computa el valor auto antes del prompt de override y lo muestra al usuario. El usuario ahora sabe exactamente qué está overrideando antes de decidir.
  4. **archiver.py**: footnote `*DCF assumptions: WACC=...%*` ahora incluye near-term growth y su source. Reliability note se muestra siempre (era condicional). `has_contradiction` corregido a buscar "spread note" en lugar de "contradiction".
  5. **value_expert.py**: bloque `## DCF GROWTH ASSUMPTION` en `_format_context()` con nivel de alerta por fuente. Rule #20 obliga al Value Expert a declarar la tasa usada, su procedencia, y su credibilidad en §6.
- **Aplicabilidad sistémica**: cualquier empresa. Especialmente crítico para small caps sin FCF positivo multi-año (fallback invisible), y para empresas donde FCF CAGR > revenue CAGR (cap silencioso).
- **Commit**: pendiente

### EC-55 — Sistema saltaba Q1 earnings completo cuando 10-K tenía < 90 días de antigüedad

- **Síntoma**: análisis de CGNX (mayo 2026) no incluía Q1 2026 earnings a pesar de que el 8-K fue publicado el 6-mayo-2026, 11 días antes del análisis. `quarterly_earnings_text=None` en el report.
- **Causa raíz**: `fetch_quarterly_snapshot()` tenía `staleness_threshold_days=90`. El 10-K de CGNX (FY2025) fue publicado en febrero 2026 — tenía ~90 días en mayo. La comprobación `age_days <= 90` devolvía `True` y el sistema saltaba el fetch del 10-Q. El press release 8-K Item 2.02 tiene un lookback separado (120 días) pero también puede fallar por P3 (ver EC-57).
- **Fix**: `staleness_threshold_days=45` como default. 45 días ≈ 1.5 meses: suficiente para que el primer 10-Q posterior al 10-K anual (que sale ~6 semanas después) active el fetch.
- **Aplicabilidad sistémica**: cualquier empresa que publique 10-K en Q1 (enero-marzo) y sea analizada en mayo-junio. Especialmente relevante para empresas con fiscal year calendar (diciembre).
- **Commit**: pendiente (MCE-27)

### EC-56 — CAGR anclado a año boom: tasa de crecimiento histórico sesgada por base cíclica alta

- **Síntoma**: CGNX análisis mayo 2026 mostraba CAGR 5y de -0.4% (o similar a cero/negativo), calculado desde FY2022 (año pico de automatización post-COVID: revenue record). El DCF proyectaba desde base inflada. El Value Expert no tenía señal de que el punto de partida era un pico cíclico.
- **Causa raíz**: `_compute_revenue_cagr_5y` calcula (newest/oldest)^(1/n)-1. Si el año más reciente está por encima del rango histórico (boom), el CAGR se deprime artificialmente porque la mediana histórica es más baja. El sistema no tenía mecanismo para detectar ni señalizar este sesgo.
- **Fix (P5)**: en `valuation_agent()`, tras calcular `near_term_growth`, si `revenue[0].value > median × 1.15` (más reciente >15% sobre la mediana histórica), se añade aviso a `dcf_reliability_note`: "Cyclical peak base: current revenue is X.Xx the historical median. CAGR-derived growth rate may be anchored to a boom year."
- **Condición de activación**: no activa si el usuario especificó `revenue_growth_yr1_pct` override (ya tomó control consciente).
- **Aplicabilidad sistémica**: cualquier empresa cíclica (industriales, semiconductores, machinery) donde el año más reciente es un pico secular post-crisis o post-boom. AZO y BBW menos afectados (revenue estable o en crecimiento suave). PRDO tampoco (educación, ciclos diferentes).
- **Commit**: pendiente (MCE-27)

### EC-57 — Item 2.02 no detectado en 8-K con descripción completa del ítem

- **Síntoma**: `fetch_earnings_press_release()` devolvía `None` para algunos filers a pesar de existir el 8-K Item 2.02. En EDGAR, el campo `items` puede contener strings como `"2.02 Results of Operations and Financial Condition"` en vez de simplemente `"2.02"`.
- **Causa raíz**: detección usaba `== "2.02"` (exact match). Cualquier string con descripción adjunta fallaba silenciosamente. El sistema pasaba al siguiente filing sin registrar el mismatch.
- **Fix (P3)**: `startswith("2.02")` tras strip de "Item ". Detecta todas las variantes conocidas: `"2.02"`, `"2.02 Results of..."`, `"Item 2.02"`, `"Item 2.02 Results of..."`. Además: `max_chars` 3000→8000 (press releases típicos ocupan 5-7KB; con 3000 chars se cortaban a mitad perdiendo guidance y métricas operativas).
- **Audit MCE-26**: búsqueda sistemática de `== "2.02"` en todo `src/` — solo aparecía en `edgar.py:fetch_earnings_press_release`. No hay otras ocurrencias del patrón.
- **Aplicabilidad sistémica**: cualquier empresa. Probabilidad más alta en filers con herramientas de filing que rellenan el campo item con el título completo según el reglamento SEC.
- **Commit**: pendiente (MCE-27)

---

## MCE-28 — PATH audit: ROIC trend, thesis-break false positive, FCF CAGR, Form 4 ordering (2026-05-22)

### EC-58 — Value Expert contradecía label ROIC RISING con narrativa de "eroding moat"

- **Síntoma**: PATH (UiPath) análisis mayo 2026 — §2/§5 del informe declaraba "moat is eroding, declining ROIC" con confianza 100%. La serie XBRL real: FY2023:-28.7% → FY2024:-12.3% → FY2025:-2.1% → FY2026:+4.7%, tendencia RISING inequívoca.
- **Causa raíz**: el Value Expert recibía la serie numérica de ROIC y el label pre-computado `ROIC trend interpretation: RISING` en el contexto, pero ninguna regla del system prompt le prohibía explícitamente contradecirlo. El LLM se fijó en el nivel absoluto (negativo) e ignoró la tendencia direccional. Sin constraint explícito, el LLM optimiza para sonar prudente ("ROIC negativo → moat débil") aunque contradiga los datos directamente.
- **Fix**: Rule 22 añadida al system prompt de `value_expert.py`: el label pre-computado XBRL es AUTHORITATIVE. Si el label es RISING, el LLM MUST NOT escribir "declining ROIC", "eroding ROIC", "falling ROIC", "moat is eroding due to ROIC decline". El texto permisible para ROIC aún negativo pero en mejora: "ROIC improving from deeply negative toward breakeven", "ROIC recovery in progress". Ver `agents/value_expert.py` — Rule 22 en `_SYSTEM_PROMPT`.
- **Audit MCE-26**: patrón revisado en `value_expert.py` — solo un punto de generación de §2/§5. No hay otros agentes que generen narrativa de ROIC trend.
- **Aplicabilidad sistémica**: cualquier empresa con ROIC en recuperación (turnaround, emerging profitability). Especialmente SaaS/tech en transición de growth a profitability (PATH, SNOW, DDOG early stages). Sin esta regla, el LLM siempre interpreta ROIC negativo como "moat weakness" aunque la dirección sea positiva.
- **Commit**: MCE-28

### EC-59 — Thesis-break self-check: falso positivo por substring matching (PATH "fall" en "falling")

- **Síntoma**: PATH marcado con `⚠️ THESIS-BREAK SELF-CHECK WARNING: revenue fall` cuando los ingresos crecían +13% YoY. Thesis-breaker PATH §9: "thesis breaks if FY2027 quarterly revenue falls below 10%". §7 del mismo análisis: "revenue growth falling toward double digits". El mismo mecanismo causó EC-45 (CGNX, "compress" en "compressing").
- **Causa raíz**: `_thesis_break_self_check` usaba Python `w in text` — substring matching, no word boundary. `"fall" in "falling"` → True. El thesis-breaker define la condición futura ("si X ocurre"), el análisis §7 describe riesgo futuro en forma progresiva ("cayendo hacia"), y Python los confundía como condición actual cumplida.
- **Fix (ADR-031)**: pipeline de dos etapas. Stage 1: `re.search(rf'\b{re.escape(w)}\b', text)` — "fall" no matchea "falls"/"falling"/"fallen". Stage 2: solo si el Python word-boundary trigger dispara, invocar LLM cheap (`gpt-4o-mini`, `settings.openai_validation_model`) con prompt binario: "¿Esta condición está ACTUALMENTE ACTIVA o es un riesgo FUTURO?". El warning solo se añade si el LLM confirma `is_currently_true=True`. Default conservador: si el LLM falla, retorna `False` (prefiere falso negativo sobre falso positivo). Ver `agents/value_expert.py` — `_word_in_text`, `_validate_thesis_break_with_llm`, `_thesis_break_self_check`. Ver también ADR-031.
- **Aplicabilidad sistémica**: cualquier empresa con thesis-breakers expresados en infinitivo/raíz ("if revenue falls", "if margins compress", "if growth declines") y análisis que describen el mismo riesgo en forma progresiva o futura. El false positive es prácticamente garantizado sin word-boundary fix.
- **Commit**: MCE-28

### EC-60 — FCF CAGR fallback usaba revenue CAGR cuando FCF más antiguo era negativo

- **Síntoma**: PATH DCF usaba tasa de crecimiento near-term de 15% (revenue CAGR). FCF real de PATH: FY2022:-$12M → FY2023:$146M → FY2024:$303M → FY2025:$421M → FY2026:$523M. CAGR real FCF (ventana FY2023-FY2026): ~53% en 3 años, pero ese era el arranque desde FCF positivo. CAGR más relevante para DCF maduro: ~9.8% (FY2023→FY2026 de $146M→$523M). El sistema ignoraba completamente los datos FCF disponibles y usaba revenue como proxy, inflando el DCF un ~35-40%.
- **Causa raíz**: `_fcf_cagr` calculaba `(newest / oldest) ^ (1/n) - 1` siempre usando el FCF más antiguo como denominador. Si `oldest <= 0`, retornaba `None`. Cualquier empresa con FCF negativo en el primer año de la serie (incluso si todos los años recientes son positivos) caía al fallback de revenue. No buscaba el primer denominador positivo disponible.
- **Fix**: `_fcf_cagr_with_window(fcf_series)` — escanea la serie hacia atrás desde el más antiguo hasta encontrar el primer valor positivo como denominador. Retorna `(cagr, n_periods)` donde `n_periods` es el tamaño real de la ventana usada. Si `newest <= 0`, retorna `(None, 0)`. `_fcf_cagr` mantiene firma de compatibilidad como wrapper. `_compute_near_term_growth` y `valuation_agent` usan `_fcf_cagr_with_window` directamente para capturar `n_periods`. Ver `agents/valuation.py:52-91`.
- **Aplicabilidad sistémica**: cualquier empresa que haya tenido FCF negativo en años pasados pero positivo en años recientes — especialmente SaaS en transición, turnarounds, empresas en early growth phase. Sin este fix, el fallback a revenue CAGR era silencioso e inflaba sistemáticamente los DCF.
- **Commit**: MCE-28

### EC-61 — Form 4 cap 20 en orden ascendente: compras recientes del CEO no detectadas

- **Síntoma**: PATH análisis mayo 2026 — el CEO compró 9.62M de acciones (transacción material, señal bullish fuerte), pero el informe no lo mencionaba. Los Form 4 feateaban ventas rutinarias de empleados de hace 12-18 meses. El campo `insider_trades` de `FilingsReport` estaba poblado pero no incluía la compra relevante.
- **Causa raíz**: `fetch_form4_transactions` aplicaba `filings[:20]` (cap de 20) antes de procesar. EDGAR devuelve Form 4 en orden cronológico ascendente por defecto (los más antiguos primero). Con 20 Form 4s, la compra reciente del CEO quedaba fuera del slice si había >20 filings históricos anteriores. Adicionalmente, `_detect_10b5_1_plan` no cubría variantes XML con checkbox (`"rule 10b5"`, `"10b-5-1"`, `"trading plan"`, `"prearranged"`).
- **Fix**: cap aumentado de 20 a 50 (`filings[:50]`). Sort explícito por `filing_date` descendente antes de procesar: las transacciones más recientes se analizan primero. Si `filing_date` no disponible, se preserva el orden original de EDGAR sin error. Expansión de `_10B5_PATTERNS` a 10 variantes incluyendo `"rule 10b5"`, `"10b-5-1"`, `"trading plan"`, `"pre-arranged"`, `"prearranged"`, `"predetermined plan"`, `"automatic plan"`. Ver `tools/edgar.py:900-910`.
- **Audit MCE-26**: patrón `filings[:20]` buscado en todo `src/` — solo ocurría en `fetch_form4_transactions`. Patrón `_10B5_PATTERNS` — solo definido en `edgar.py`.
- **Aplicabilidad sistémica**: cualquier empresa con alta actividad de Form 4 (large caps con muchos insiders reportando) o donde un insider principal haya realizado una transacción material recientemente. Con cap=20 y orden ascendente, el sistema era estructuralmente ciego a las últimas semanas de actividad insider en empresas activas.
- **Commit**: MCE-28

### EC-62 — EV/EBIT negativo clasificado como "cheap" vs peers

- **Síntoma**: SYM (EBIT = -$115M, EV ~$28B) → EV/EBIT = -48.5x. Sistema clasifica como `"cheap"` vs peers con mediana 31.4x. El informe muestra "cheap" en la convergence table, lo que es económicamente absurdo.
- **Causa raíz**: `_classify_ev_ebit_vs_peers` no guardaba contra `subject_ev_ebit <= 0`. Con EBIT negativo, EV/EBIT es negativo. `negative / 31.4 = -1.54`, y `-1.54 < 0.70` → `"cheap"` por lógica de ratio. El guard existente solo cubría `peer_ev_ebit_median <= 0`.
- **Fix**: añadir guard explícito `if subject_ev_ebit is None or subject_ev_ebit <= 0: return "n/a"` antes de calcular el ratio. Ver `agents/valuation.py:_classify_ev_ebit_vs_peers`.
- **Aplicabilidad sistémica**: cualquier empresa con EBIT negativo (growth-stage, turnaround, año de reestructuración). Sin este fix, la clasificación era sistemáticamente incorrecta para toda empresa no-profitable.
- **Commit**: MCE-29

### EC-63 — DCF upside % calculado desde market_cap stale (Finnhub basicFinancials)

- **Síntoma**: SYM análisis muestra DCF per share = $48.91 con upside +331.9%. Precio actual = $53.63. El $48.91 debería implicar ligera sobrevaluación (-8.8%), no megaupside. El precio base implícito del cálculo era ~$11.33 (precio post-crash noviembre 2024).
- **Causa raíz**: `dcf_upside = (equity_value - market_cap) / market_cap`. `market_cap` proviene de `Finnhub basicFinancials`, que devuelve el market cap a fecha del último filing trimestral — puede estar 6-18 meses desactualizado. `price_current` proviene de `Finnhub quote()` — siempre real-time. Para SYM, `basicFinancials` tenía el market cap del crash de noviembre 2024 (~$11/share) mientras la acción ya había recuperado a $53.63. El `dcf_per_share` era correcto; el `%` era incorrecto porque usaba denominadores de fuentes distintas.
- **Fix**: calcular `dcf_upside` desde valores per-share cuando estén disponibles: `(dcf_per_share.value - price_current.value) / price_current.value`. Fallback a `(equity_value - market_cap) / market_cap` solo si `dcf_per_share` o `price_current` no están disponibles. Mismo patrón aplicado a `epv_upside` en `_compute_epv` (nuevo parámetro `price_current`). Ver `agents/valuation.py:~L1030,~L382`.
- **Aplicabilidad sistémica**: cualquier empresa con alta volatilidad de precio donde `basicFinancials` y el precio actual difieran significativamente. Post-crash, post-spike, post-M&A. El upside % podía ser radicalmente incorrecto en ambas direcciones.
- **Commit**: MCE-29

### EC-64 — WACC sensitivity table ±1% insuficiente para empresas de alto riesgo

- **Síntoma**: SYM análisis con WACC 14% muestra sensitivity 13-15%. Para evaluar SYM correctamente se necesita ver valores a 17-18% WACC (85% concentración cliente, SEC investigation activa, restatements). Con ±1%, el analista nunca ve el rango de interés.
- **Causa raíz**: `_sensitivity_table` usaba `w_delta in (-0.01, 0.0, 0.01)`. Para empresas con WACC apropiado de 17%+ y advisory de 9.7%, la tabla solo cubría 13-15% — exactamente el rango que el analista ya había explorado manualmente.
- **Fix**: cambiar a `w_delta in (-0.02, 0.0, 0.02)`. Tabla 3×3 ahora cubre WACC ±2pp. Cuando `abs(wacc_usado - wacc_advisory) > 3pp`, el sistema añade nota en `dcf_reliability_note` indicando la divergencia y recordando que el advisory usa β=1 sin company-specific risk premium. Ver `agents/valuation.py:_sensitivity_table` y bloque EC-64.
- **Aplicabilidad sistémica**: empresas de alto riesgo idiosincrático donde el WACC apropiado supera en ≥3pp el advisory (concentración cliente, governance issues, recent IPO, capital-intensive + single customer).
- **Commit**: MCE-29

### EC-65 — FCF inflado por advance payments usado como base DCF sin advertencia

- **Síntoma**: SYM FY2025: Revenue $2.247B, EBIT -$115M, FCF $825M → FCF margin 36.7%. Gross margin ~20%. Un FCF margin 1.8× el gross margin es imposible desde operaciones normalizadas. Sistema usaba $825M como `dcf_base` sin aviso.
- **Causa raíz**: no existía mecanismo para detectar FCF anómalamente alto vs la economía declarada del negocio. FCF > gross_profit es económicamente imposible desde operaciones puras.
- **Fix**: nueva función `_fcf_quality_flag(fcf_latest, revenue_latest, gross_profit_latest)`. Flag cuando FCF/Revenue > 25% Y FCF margin > gross_margin × 1.3. Advertencia en `dcf_reliability_note` y campo separado `fcf_quality_warning` en `ValuationReport`. No modifica el DCF. Ver `agents/valuation.py:_fcf_quality_flag`, `schemas/reports.py:ValuationReport`.
- **Aplicabilidad sistémica**: project-based businesses con advance payments (defense, large-scale systems integrators, construction, enterprise software con multi-year contracts prepagados).
- **Commit**: MCE-29

### EC-66 — Governance red flags (SEC investigation, restatements) no capturadas

- **Síntoma**: SYM análisis reporta "no Item 4.02 restatement found". Pero SYM tenía: restatement de 3 trimestres FY2024 (dic 2024), investigación SEC activa, material weaknesses, class action feb-nov 2024. Nada aparecía en el informe.
- **Causa raíz (dual)**: (1) `fetch_8k_events` con ventana de 12 meses no alcanzaba los 8-Ks de diciembre 2024 cuando el análisis se corría 17 meses después. (2) El prompt del filings agent no pedía explícitamente SEC investigations activas, material weaknesses, class actions, ni whistleblower issues — el LLM los diluía como "riesgo genérico".
- **Fix**: (1) `edgar.py`: nuevo parámetro `restatement_months=24` en `fetch_8k_events`. Item 4.02 usa look-back de 2 años; el resto usa `months` estándar. Un solo round-trip a EDGAR. (2) `filings.py`: `_SYSTEM_PROMPT` añade sección GOVERNANCE RED FLAGS obligatoria con 5 items. User prompt añade preguntas 10-14. Nuevo campo `governance_red_flags: Optional[str]` en `FilingsRAGReport`. Ver `tools/edgar.py:fetch_8k_events`, `agents/filings.py:_SYSTEM_PROMPT`, `schemas/reports.py:FilingsRAGReport`.
- **Aplicabilidad sistémica**: cualquier empresa con governance issues en los últimos 2 años. Sin este fix, el sistema era estructuralmente ciego a restatements históricos e investigaciones activas.
- **Commit**: MCE-29

### EC-67 — HRL (Hormel): revenue concept not found — cadena anual incompleta vs quarterly

- **Síntoma**: `DataUnavailableError: HRL: revenue concept not found in income statement` al intentar analizar Hormel Foods Corp (ticker HRL). El DD se interrumpía en el nodo `financials`.
- **Causa raíz**: la cadena de fallback XBRL para revenue *anual* (`edgar.py:288-292`) solo intentaba 2 conceptos: `RevenueFromContractWithCustomerExcludingAssessedTax` y `Revenues`. Hormel usa `SalesRevenueNet` — el concepto legacy pre-ASC 606 estándar para empresas CPG/food. La función de quarterly snapshot (línea ~710) ya tenía 4 fallbacks incluyendo `SalesRevenueNet`, pero la cadena anual nunca se había igualado. Inconsistencia estructural entre las dos cadenas.
- **Fix**: cadena anual expandida a 6 conceptos: añadidos `SalesRevenueNet` (legacy CPG/food), `RevenueFromContractWithCustomerIncludingAssessedTax` (ASC 606 con IVA), `SalesRevenueGoodsNet` (product-only), `NetSales` (industriales/food). Quarterly igualada con los mismos 6 conceptos. Ver `tools/edgar.py:287-299` y `tools/edgar.py:709-715`.
- **Audit MCE-26**: `RevenueFromContractWithCustomerExcludingAssessedTax` aparece en exactamente 2 sitios de `src/` — ambos corregidos en este fix.
- **Aplicabilidad sistémica**: cualquier empresa CPG, food & beverage, o industrial que adoptó XBRL antes de ASC 606 (2018) puede usar `SalesRevenueNet` o `NetSales`. Sin este fix, el sistema rechazaba un conjunto amplio de empresas del universo value (consumer staples, food & beverage son sectores canónicos de Buffett/Munger).
- **Commit**: pendiente

---

## H1 — Integridad (2026-10, plan `docs/superpowers/plans/2026-10-03-horizonte1-integridad.md`)

### EC-71 — Fallback de proveedor ante cualquier excepción, coste de Claude no contabilizado y temperature rechazada
- **Síntoma**: `complete()` caía a OpenAI ante cualquier `Exception` de Anthropic, incluidos bugs propios; `_complete_anthropic` no registraba el uso; `_MODEL_PRICING` no tenía modelos Claude (coste ≈ 0); `RunStats` no decía qué modelo había respondido. Además, los modelos Claude actuales (Opus 4.7+, Sonnet 5.5, Fable) devuelven 400 si se envía `temperature`, y con thinking activo `content[0]` no es texto.
- **Causa raíz**: wrapper diseñado para un único caso feliz; sin errores tipados (`LLMError` nunca se lanzaba).
- **Fix**: `llm/errors.py` con jerarquía tipada; `LLMClient.structured()` con fallback solo ante `LLMProviderError(fallback_eligible=True)`; `_record_usage` único (incluye Claude); precios Claude con resolución por prefijo (exacto primero); `RunStats.calls_by_model` + `LLMClient.last_model_used`; `temperature` solo a familias que la aceptan; texto del primer bloque `type == "text"`. Ver ADR-032.
- **Commit**: `3f4388f`

### EC-89 — Tests con llamadas de red reales ocultas por el `.env` local
- **Síntoma**: en un checkout sin `.env` fallaban 4 tests; con `.env` pasaban haciendo llamadas reales a OpenAI (end-to-end de grafo, validador de thesis-break) y a Finnhub/EDGAR (agente de mercado). La suite tampoco arrancaba sin `.env` porque `Settings()` exige `SEC_USER_AGENT` al importar.
- **Causa raíz**: mocks incompletos del fan-out de especialistas y del validador ADR-031; `set_test_env` corría después de importar `due_diligence.config`.
- **Fix**: `tests/conftest.py` fija `SEC_USER_AGENT` a nivel de módulo y añade el fixture autouse `block_real_network_clients` (constructores de `openai.OpenAI`, `anthropic.Anthropic` y `tavily.TavilyClient` lanzan error); helper `_mock_specialists` en `tests/test_graph_1a.py`; validador mockeado en los tests de thesis-break.
- **Commit**: `3f4388f`


### EC-68 — Fallos de parseo del LLM disfrazados de contenido real ("HOLD fantasma")
- **Síntoma**: cuando el LLM devolvía JSON truncado o mal formado, el report del especialista salía con `stub=False` y el texto `"[Parse error] Raw LLM response: ..."` dentro de `mda_highlights` (filings) o campos vacíos (management, competitive, critic). El Value Expert, ante el mismo fallo, producía un `verdict="HOLD"` de relleno y el archivo se llamaba `...-HOLD.md` / `...-PENDING.md`. Un fallo técnico se archivaba como una decisión de inversión.
- **Causa raíz**: (1) `complete()` devolvía texto libre y cada agente recortaba el JSON con `find("{")`/`rfind("}")` + `json.loads` dentro de un `except Exception` que nunca propagaba; (2) el report no tenía forma de expresar "corrió y falló" (solo `stub`); (3) `complete()` hacía fallback de proveedor ante cualquier excepción, incluidas truncaciones.
- **Fix**: `LLMClient.structured()` con structured outputs nativos y errores tipados (`llm/errors.py`, T1); schemas de salida estrictos en `schemas/llm_outputs.py` (T2a); cada agente captura `LLMError` y devuelve `stub=True, error="<Clase>: msg"` (T2b filings, T2c management/competitive/critic, T2d Value Expert sin VERDICT y con un único reintento solo ante `LLMSchemaError`); el archiver nombra el report `-INCOMPLETE.md` y lista los fallos; la CLI muestra panel rojo "ANALYSIS INCOMPLETE" (T2e). ADR-032.
- **Commit**: `2213879` (T2b), `cb3f4b4` (T2c), `d32a753` (T2d), `cb5b04b` (T2e)

### EC-69 — Citas nunca validadas y números del LLM presentados como dato
- **Síntoma**: el invariante 2 (CLAUDE.md) y ADR-005 decían que toda cita se validaba por substring-match contra el filing; en el código no existía ningún validador. Las citas llegaban al report y al contexto del Value Expert tal como las emitía el LLM (incluidas paráfrasis y excerpts inventados). Además `insider_ownership_pct`, `ceo_tenure_years`, `form4_transactions_12m` y `backlog_usd` los generaba el LLM y se presentaban como datos ("Insider ownership (DEF 14A)"), y `insider_transaction_pattern` —texto libre del LLM— se etiquetaba "(Form 4)" (origen del caso KBR CEO/CFO).
- **Causa raíz**: la validación post-hoc se diseñó en Fase 0 (ADR-005) pero nunca se implementó; los agentes construían `Citation` directamente desde el JSON del LLM, y los schemas permitían al LLM rellenar campos numéricos sin evidencia. Durante ~6 meses la documentación afirmó una validación inexistente.
- **Fix**: `validation/citations.py` (T3a): substring normalizado (NFKC, comillas/guiones tipográficos, minúsculas, espacios) o `rapidfuzz.partial_ratio ≥ 90`; todos los números del excerpt deben estar en la ventana alineada; excerpt < 20 caracteres → rechazado; corpus vacío → todas rechazadas con nota. Rechazadas: `verified=False`, `confidence=0`, `verification_note`. `gate_llm_value`: un número del LLM solo se conserva si su cita se verifica Y contiene el número (a escala ×1/×1e3/×1e6/×1e9). T3b: validación dentro de cada nodo con el corpus local; `form4_transactions_12m = len(form4_transactions)` (determinista); ownership y tenure solo contra fuentes primarias (DEF 14A, 10-K Item 10, Form 4), nunca Tavily/transcripts; `source_url` normalizado a la etiqueta real del corpus. T3c: el contexto del Expert y la sección "Sources & Citations" solo muestran citas verificadas; las rechazadas van a "## Unverified citations (excluded from synthesis)"; narrativa LLM etiquetada "(LLM summary, unverified)". Corrección en ADR-005.
- **Pendiente (H2)**: validación semántica claim↔excerpt (un excerpt real puede no sostener el claim, p.ej. CEO vs CFO).
- **Commit**: `fbcce66` (T3a), `61eea27` (T3b), `bd7fbe9` (T3c)

### EC-70 — Datos extraídos que nunca llegaban a la síntesis
- **Síntoma**: `governance_red_flags` (EC-66: investigaciones SEC, material weaknesses, restatements, class actions) se extraía del 10-K pero ningún consumidor lo leía: ni el Value Expert, ni el Critic, ni el report. Las respuestas socráticas del checkpoint 1 (`answers`) no llegaban al Expert, y `growth_justification` (checkpoint 2) solo aparecía si existía `historical_cagr_5y`, mezclada con la evidencia en el bloque HISTORICAL CONTEXT.
- **Causa raíz**: sin contrato productor→consumidor: un campo nuevo en un report no obliga a nadie a consumirlo, y ningún test lo detectaba. Las reglas de prompt (23/24) no tenían check determinista.
- **Fix**: T4a — bloque `## GOVERNANCE RED FLAGS (from 10-K)` en el contexto del Expert y en el prompt del Critic, sección `## Governance Red Flags (10-K)` en el report, regla 23 en el prompt; `FilingsLLMOutput.active_sec_investigation`/`material_weakness` + `governance_citation`: los flags `True` solo sobreviven con cita verificada y activan un tope determinista `verdict_confidence ≤ 0.6`; check post-output: si §7 no nombra un flag verificado → caveat "⚠️ §7 omits governance flag". T4b — `_format_investor_hypotheses`: bloque `## INVESTOR'S OWN HYPOTHESES (evaluate critically, do not adopt)` al final del contexto con respuestas no vacías y `growth_justification` (aunque no haya CAGR); regla 24 + check determinista de etiquetas SUPPORTED/CONTRADICTED/UNTESTABLE en §8. T4c — guard test `tests/test_schema_consumers.py`: todo campo de los reports especialistas y de `FinancialsReport`/`CriticReport` tiene consumidor (value_expert, critic, archiver, cli) o una excusa documentada en `_ALLOWLIST`; numeración "Rule 18" → "Rule 19" corregida (ver EC-53).
- **Commit**: `4dde992` (T4a), `6ce615c` (T4b), `61fac3f` (T4c)

### EC-72 — DCF descontaba FCF apalancado al WACC sin bridge
- **Síntoma**: valor por acción del DCF +18 % en una empresa apalancada (ejemplo A: $222,22 vs $195,37 correcto) y −21 % en una con caja neta (ejemplo C: $24,79 vs $31,35); `_mv_val(financials.diluted_shares)` sobre una lista habría dado `AttributeError` en cada run.
- **Causa raíz**: `FCF = OCF − capex` (ya neto de intereses) descontado a una tasa llamada WACC y presentado como equity sin restar deuda ni sumar caja/STI/LTI; acciones = portada sin dilución.
- **Fix**: `valuation_agent` → FCFF normalizado (mediana del margen 3 años) → `dcf_components` con valor terminal con reinversión → EV capitalizado a la fecha de valoración → `equity_bridge` (deuda, NCI, preferentes, caja, STI, LTI + participaciones) → `resolve_share_count` (portada × diluidas/básicas) → upside por acción. `ValuationInputError` capturado → `ValuationReport(stub=True, error=...)`. Test de integración `parse_financials → valuation_agent` con STI > 0.
- **Commit**: `a547e9a` (T12a)

### EC-73 — EPV upside comparaba EV con market cap sin precio
- **Síntoma**: sin precio (o con shares ausentes) el upside del EPV se calculaba como (EPV_enterprise − market cap)/market cap: EV contra equity, sesgado por toda la deuda neta. El EPV además restaba solo deuda − caja (sin STI/LTI/NCI/preferentes) y usaba `1 − NI/EBIT` como tipo.
- **Causa raíz**: fallback "indicativo" que mezclaba planos; bridge del EPV distinto del (inexistente) del DCF.
- **Fix**: `_compute_epv(ebit_series, wacc, net_claims, shares, price_current, mid_year, *, capitalization)` → mismo `net_claims` que el DCF, tipo marginal, misma capitalización a fecha de valoración, upside solo por acción (None + nota sin precio), `epv_growth_premium = 1 − EPV/sh ÷ precio` (Greenwald).
- **Commit**: `fe510d2` (T12b)

### EC-74 — Reverse DCF buscaba el market cap con un modelo de EV
- **Síntoma**: el growth implícito salía infraestimado en empresas apalancadas y sobreestimado con caja neta; los múltiplos EV usaban `market_cap + deuda − caja` (STI ignorada) y EV/FCF apalancado; peers con otra definición de EV.
- **Causa raíz**: target equity (market cap) frente a un DCF que produce EV; cada consumidor tenía su "net debt".
- **Fix**: target = market EV (precio × acciones + `net_claims`), deshecha la capitalización, misma función `dcf_components` (incluida la reinversión terminal y mid-year) que el DCF directo; None si la solución toca los bordes de búsqueda o market EV ≤ 0 ("net-net", múltiplos n/m). EV/EBIT, EV/FCFF, earnings yield, P/E (equity de mercado / NI), `peers.py` y `critic.py` usan `net_claims`.
- **Commit**: `fe510d2` (T12b)

### EC-75 — "Histórico" de convergencia era near_term_growth (circular)
- **Síntoma**: la fila Reverse DCF de la convergencia comparaba el growth implícito con el mismo FCF CAGR que alimentaba el DCF directo; además comparaba un g1 de stage 1 (con fade posterior) contra una CAGR plana, y el EPV marcaba "expensive" con cualquier upside < 0 (asimétrico). La sensibilidad mostraba "equity" total en USD B sin bridge.
- **Causa raíz**: `historical_cagr_for_conv = near_term_growth`; umbral EPV `< 0`; `_sensitivity_table` sobre `_three_stage_dcf` sin bridge ni acciones.
- **Fix**: histórico = `historical_cagr_5y` (CAGR de ingresos); clasificación del reverse sobre la CAGR equivalente a 10 años del camino implícito (`_ten_year_equivalent_cagr`, `reverse_dcf_10y_equivalent`); banda EPV ±10 % simétrica; sensibilidad en USD/share por el mismo camino que el titular (reinversión terminal, capitalización, bridge), celdas inválidas `None`, `sensitivity_unit="USD/share"`; ADR-030 con la tabla de umbrales corregida.
- **Commit**: `b0d62ca` (T12c)

### EC-76 — WACC ≤ g recortado en silencio
- **Síntoma**: con WACC 3 % y g 2,5 % el DCF devolvía valores absurdos (TV ≈ 200× FCF) sin aviso; con WACC = g, el código recortaba g a WACC − 0,5 pp.
- **Causa raíz**: `_three_stage_dcf` "arreglaba" el input inválido (`terminal_growth = wacc - 0.005`) en lugar de fallar.
- **Fix**: `valuation_math.validate_dcf_rates` lanza `ValuationInputError` si WACC ≤ 0 o WACC − g < 1 pp (con `round(…, 9)` para tolerar coma flotante); `_sensitivity_table` convierte las celdas inválidas en `None`; el agente (T12a) captura y devuelve `ValuationReport(stub=True, error=...)`; checkpoint 2 y la CLI re-preguntan (T11).
- **Commit**: `cbbdea7` (T10), `f91e677` (T11), `a547e9a` (T12a)

### EC-77 — Sesgos de inputs del FCF y del tipo impositivo
- **Síntoma**: FCF sobreestimado (SBC sumado como no-caja), caja contada dos veces (intereses cobrados dentro del OCF + caja sumada en el bridge), tipo impositivo sobreestimado (EBIT 300, int 40, NI 195 → 35 % en vez de 25 %); EPV inflado en cíclicas por descartar años con EBIT negativo.
- **Causa raíz**: `t = 1 − NI/EBIT` mete intereses y NCI en el impuesto; el FCF apalancado no restaba SBC ni intereses cobrados; `_ebit_median` filtraba `value > 0`.
- **Fix**: `fcff_breakdown` (SBC como coste, interest income neto restado, principal de leasing financiero restado); tipo canónico `effective_tax_rate_multi` y `MARGINAL_TAX_RATE` para perpetuidades; `_ebit_median` incluye negativos y devuelve None si la mediana ≤ 0. Los inputs (intereses, SBC, D&A, acciones ponderadas, principal de leasing) se extraen en T8.
- **Commit**: `57e8c27` (T8), `cbbdea7` (T10)

### EC-78 — `_get_point` devolvía un año antiguo etiquetado como el actual
- **Síntoma**: un concepto de balance sin dato en el último ejercicio (columna `FY 2025` = NaN) aparecía en el report con el valor de FY2024 pero etiquetado `FY2025`. Deuda/caja/activos de un año antes presentados como actuales, sin aviso.
- **Causa raíz**: `_get_point(df, concept, period)` iteraba las columnas FY y devolvía el primer valor no-NaN, poniéndole la etiqueta `period` pedida. El parámetro `period` solo se usaba como etiqueta, nunca para seleccionar.
- **Fix**: `xbrl_extract.point_value(df, concepts, period, facts)` → `select_period_value` lee la columna EXACTA del periodo; si falta, raw facts emparejados por `period_end` (`EdgarFactsLookup`); si tampoco, `None` (nunca un año distinto). Test `test_point_value_respects_period_instead_of_returning_stale_year`.
- **Commit**: `6d13f52` (T5)

### EC-79 — "CAGR 5 años" calculado con 3 años: `periods=4` por defecto
- **Síntoma**: `revenue_cagr_5y` y las series anuales nunca tenían más de 4 años; el "CAGR 5y" era en realidad de 3 intervalos.
- **Causa raíz**: `facts.income_statement()/balance_sheet()/cashflow_statement()` tienen `periods: int = 4` por defecto en edgartools. El `n=10` de `_get_annual` nunca tuvo efecto porque el DataFrame ya venía recortado.
- **Fix**: `fetch_raw` pide `periods=10` a los tres estados. T7 fija las ventanas de los consumidores con `max_years=5` para no cambiar silenciosamente los defaults a CAGRs de 9 años.
- **Commit**: `6d13f52` (T5)

### EC-80 — Deuda doble-contada o infravalorada
- **Síntoma**: `total_debt` inflado en empresas que reportan a la vez `LongTermDebt` (que YA incluye la porción corriente) y `LongTermDebtCurrent`/`DebtCurrent`; o infravalorado cuando la deuda corriente venía solo como `CommercialPaper` (no estaba en la cadena) o cuando el LTD venía como `...AndCapitalLeaseObligations` y además se sumaban leasings.
- **Causa raíz**: la cadena ST sumaba TODOS los conceptos presentes (`ShortTermBorrowings + LongTermDebtCurrent + ... + DebtCurrent`), mezclando un total (`DebtCurrent`) con sus componentes; la cadena LT tomaba `LongTermDebt` (total incl. corriente) como "largo plazo". Un total se sumaba con sus componentes.
- **Fix**: `xbrl_extract.resolve_debt` con política exclusiva: LT = noncurrent explícito, si no `LongTermDebt − porción corriente` (inferida de `DebtCurrent − borrowings` si no hay `LongTermDebtCurrent`, con flag); ST = `DebtCurrent` si existe, si no primer borrowing (exclusivos) + porción corriente; leasing financiero solo si el concepto de deuda no lo incluye. Identidades (`LongTermDebt ≈ noncurrent + current`, `DebtCurrent ≥ LongTermDebtCurrent`) se reportan en `data_quality_flags`. `total_debt = LT + ST + finance_leases`. Ejemplo: LTD 90, noncurrent 78, current 12, CP 8 → antes ST = 12 (+ CP ausente) y LT = 90 → 102; ahora 78 + (12 + 8) = 98. Tras T14 (edgartools 5.59.1 + fixtures regenerados) AAPL pasa de 90,678B a 99,887B porque ahora entra el papel comercial.
- **Commit**: `24e791d` (T6), `5a3000c` (T14, fixtures)

### EC-81 — Caja = 0 en silencio, STI hardcodeado a 0 y doble resta de STI
- **Síntoma**: (1) si ningún concepto de caja estaba en la vista curada, `cash_and_equivalents = 0.0` sin aviso → EV e IC sobrestimados; (2) `short_term_investments` siempre 0 (AAPL tiene ~$35B de valores negociables corrientes); (3) `cash_and_equivalents = cash + STI` en `parse_financials`, de modo que un bridge que restase STI aparte la habría restado dos veces.
- **Causa raíz**: defaults silenciosos (`{"value": 0.0}`) en lugar de fail loud; STI marcado como "no aparece en el BS normalizado" sin buscar conceptos alternativos ni raw facts; semántica de `cash_and_equivalents` ambigua.
- **Fix**: `resolve_cash` (estándar → restricted cash marcado en `source` → `DataUnavailableError`); STI/LTI extraídos con cadenas propias y raw facts; `cash_and_equivalents` = SOLO caja, `short_term_investments`/`long_term_investments` campos propios; consumidores actualizados (Value Expert "Cash (ex-STI)" + línea STI, CLI ídem, Critic `net_debt = deuda − caja − STI`); `balance_identity_flags` (Assets == L+SE estricta → raise; Assets ≈ L + E → flag). La CLI muestra `DataUnavailableError` como panel rojo "DATA UNAVAILABLE" (exit 1).
- **Commit**: `24e791d` (T6)

### EC-82 — FCF emparejaba OCF y capex de años distintos
- **Síntoma**: con una serie con hueco (BBW: capex con FY2025 y OCF sin él, o viceversa), `FCF[FY2024] = OCF[FY2024] − capex[FY2025]`; márgenes brutos/operativos de Value Expert, Critic y Competitive con el mismo problema.
- **Causa raíz**: `zip(ocf_list, capex_list)` empareja por POSICIÓN; asume que ambas series tienen exactamente los mismos años en el mismo orden.
- **Fix**: `utils/periods.align_by_period` (empareja por etiqueta FY, omite periodos sin par) en `parse_financials` (FCF), `value_expert` y `critic` (margen bruto), `competitive` (márgenes bruto y operativo). Capex con `abs()` / `_mv_list_abs` (edgartools 5.5x lo devuelve con signo). Test `test_align_by_period_never_pairs_different_years`, `test_margins_aligned_by_period_not_position`.
- **Commit**: `9a0a417` (T7)

### EC-83 — CAGR por distancia de índice, no por años fiscales
- **Síntoma**: BBW (falta FY2025): revenue `[FY2026, FY2024, FY2023, FY2022]` → el índice 3 se trataba como 3 años cuando son 4 → CAGR sobrestimado. Mismo patrón en el FCF CAGR (`valuation._fcf_cagr_with_window`) y en el criterio "Revenue 3y CAGR" de Competitive (`revenue[3]`).
- **Causa raíz**: exponente `1/índice` en lugar de `1/(año_reciente − año_antiguo)`.
- **Fix**: `utils/periods.cagr_by_fiscal_years(series, max_years, scan_for_positive_start)`; `financials._compute_revenue_cagr_5y` → ventana de 5 años fiscales reales (decisión de producto: antes era un "5y" de como mucho 4 intervalos); `_fcf_cagr_with_window` → `max_years=5` + scan EC-60; Competitive → ventana de 3 años fiscales exactos. Ventanas de consumidores fijadas por test (`tests/test_series_windows.py`).
- **Commit**: `9a0a417` (T7)

### EC-84 — El snapshot trimestral nunca se producía + test tautológico
- **Síntoma**: `quarterly_snapshot` siempre ausente; log `quarterly_snapshot_no_quarters` en todos los runs; el contexto del Value Expert nunca tuvo datos del 10-Q aunque el 10-K tuviera > 45 días.
- **Causa raíz**: `get_quarterly_financials()` devuelve el XBRL del 10-Q, cuyo DataFrame tiene `concept` como columna con prefijo (`us-gaap_Revenues`) y columnas de periodo `"2026-06-27 (Q3)"`/`"(YTD)"` (verificado en vivo). El código filtraba `c.startswith("Q")` y buscaba conceptos en el índice → siempre vacío. Secundario: orden por string (`'Q4 2025' > 'Q1 2026'`). El test `test_stale_10k_beyond_45_days_triggers_fetch` afirmaba `get_filings.called or result is None` → pasaba siempre.
- **Fix**: `xbrl_extract.latest_quarter_column` (ordena por fecha, ignora YTD, acepta ambos formatos) + `quarter_value` (normaliza `concept` columna→índice sin prefijo, excluye filas dimensionales); revenue con la misma cadena que el anual (`REVENUE_CONCEPTS`). Test con el formato real; test tautológico reescrito. Efecto colateral: los tests que corrían `financials_node` con `fetch_raw` mockeado hacían una llamada REAL a EDGAR para el trimestral → `conftest.py` añade un autouse que mockea `fetch_quarterly_snapshot` salvo en tests marcados `@pytest.mark.real_quarterly_fetch`.
- **Commit**: `9a0a417` (T7)

### EC-85 — ROIC histórico con IC y tasa del último año
- **Síntoma**: la serie ROIC de 4 años dividía el NOPAT de cada año entre el IC del ÚLTIMO balance y aplicaba la tasa impositiva del último año a todos: una empresa que duplicó su capital aparecía con ROIC histórico inflado (capital de hoy, beneficios de ayer) y la "tendencia" era casi solo la del EBIT. El contexto del Value Expert llamaba "marginal ROIC > average" a esa tendencia.
- **Causa raíz**: el raw solo tenía el balance del último año (no había `balance_history`) y `parse_financials` calculaba un único `effective_tax_rate` local.
- **Fix**: `build_raw` emite `balance_history` (un balance por columna FY con las mismas reglas de deuda/caja/STI); `parse_financials` calcula `ROIC_t = NOPAT_t / media(IC_t, IC_{t−1})` con la tasa del año (`effective_tax_rate` canónica importada de `xbrl_extract`); `edgar.invested_capital(...)`: primaria Equity + Debt − Cash − STI − LTI, operativa Assets − Cash − STI − LTI − ROU − NIBCL, un solo método por empresa (si algún año tiene equity ≤ 0 → operativa para toda la serie), IC < 5 % de activos → n/m; `incremental_roic` = ΔNOPAT/ΔIC ≤ 3 años (None si ΔIC ≤ 5 %); series `total_debt_series`, `cash_series` (caja + STI), `equity_series` para `suggest_wacc`. Regla 22 del Value Expert reescrita sobre el ROIC incremental real. AZO: IC operativo explícito (método `operating:`), 6,369B tras regenerar fixtures en T14; AAPL ROIC 272 % con IC 41,2B (sin caja ni valores negociables).
- **Commit**: `b51c055` (T9), `5a3000c` (T14, fixtures)

### EC-86 — `/1e9` residual en las pistas del checkpoint 2
- **Síntoma**: las pistas del checkpoint 2 mostraban pequeñas capitalizaciones como `$0.0B`.
- **Causa raíz**: remanente del patrón EC-14/EC-17 (`/1e9` manual en lugar de `fmt_usd`) que escapó a las auditorías anteriores.
- **Fix**: las pistas usan `fmt_usd`; auditoría MCE-26 del patrón en `src/`.
- **Commit**: `69ccc00`

### EC-87 — `fetch_raw` podía usar un 10-K/A y una etiqueta FY distinta a la de las series
- **Síntoma**: `source_filing`/etiquetas del balance calculadas como `FY{period_of_report[:4]}` desde `filings.latest()`, que incluye 10-K/A; con cierres a principios de enero (52/53 semanas) la etiqueta no coincidía con la columna de edgartools; nada comprobaba que el "último 10-K" y la columna más reciente de los estados fueran el mismo año.
- **Causa raíz**: (1) `filings.latest()` en lugar de `_get_latest_full_10k` (EC-27 solo se aplicó a texto, no a números); (2) etiqueta FY por los 4 primeros caracteres del `period_of_report` en lugar de la regla de edgartools (cierre 1–7 enero → año anterior); (3) sin invariante entre ambos.
- **Fix**: `_get_latest_full_10k` en `fetch_raw`; `fy_label_from_period_end` replica la regla de edgartools; `build_raw` lanza `DataUnavailableError` si la etiqueta del 10-K ≠ la columna más reciente del income statement. Tests `test_fy_label_from_period_end_mirrors_edgartools`, `test_build_raw_fails_loud_when_statements_latest_column_differs_from_10k`.
- **Commit**: `6d13f52` (T5)

### EC-88 — Huecos de años en series anuales (BBW sin FY2025)
- **Síntoma**: series anuales con años interiores ausentes (BBW FY2025) aunque el dato existe en companyfacts.
- **Causa raíz**: el validador fiscal_year/period_end de edgartools o el umbral de < 5 facts descartan la columna del año en la vista curada.
- **Fix**: `xbrl_extract.backfill_gaps(series, facts)`: rellena solo años INTERIORES desde raw facts del mismo concepto emparejando por `period_end` (`EdgarFactsLookup`), con `source = "facts:<concepto>"`; no extrapola ni toca series derivadas. `build_raw` lo aplica a cada serie anual. Ampliado en EC-90 (rejilla tras el backfill).
- **Pendiente conocido**: restatements — el backfill toma el filing más reciente y puede mezclar años re-expresados con no re-expresados.
- **Commit**: `9a0a417` (T7), `3376586` (T14)

### EC-90 — Cadena de conceptos XBRL elegía el primer concepto con ALGÚN dato aunque fuera de hace una década
- **Síntoma**: PRDO capex solo tenía FY2016 (`PaymentsToAcquirePropertyPlantAndEquipment`); el capex reciente estaba en `PaymentsForCapitalImprovements` (solo en raw facts) → con alineación por periodo el FCF de PRDO quedaba en un único año, FY2016 (verificado en vivo 2026-10-03). Además la rejilla de etiquetas de OCF/capex se tomaba antes de rellenar el revenue: BBW FY2025 aparecía en revenue pero no en OCF/capex/FCF ni en `balance_history`.
- **Causa raíz**: `annual_series` devolvía el primer concepto de la cadena con algún dato; la rejilla se calculaba antes del backfill; `balance_history` recorría solo columnas curadas.
- **Fix**: `xbrl_extract.merge_annual` fusiona la cadena año a año (curado → facts), usado por OCF, capex, recompras, dividendos e inputs FCFF; capex añade `PaymentsForCapitalImprovements` y `PaymentsToAcquireProductiveAssets`; la rejilla se calcula tras el backfill y `balance_history` recorre la misma rejilla. edgartools actualizado 5.30.0 → 5.59.1. Verificado en vivo: PRDO FCF FY2025 216,7M; BBW FY2025 presente.
- **Commit**: `3376586`

### EC-91 — Revisión final de la rama H1: ocho defectos de datos y valoración que los tests no cubrían
- **Síntoma** (encontrados en la revisión de toda la rama, no en producción): (1) un input del FCFF ausente en un año concreto (capex FY2024) contaba 0 sin aviso; (2) el 25 % por defecto del tipo impositivo se mostraba igual que un tipo calculado; (3) el valor terminal usaba el tipo efectivo del año en vez del marginal; (4) la cobertura de intereses del Kd podía emparejar EBIT e intereses de años distintos; (5) EV/EBIT con EBIT = 0 lanzaba ZeroDivisionError y con EBIT < 0 daba un múltiplo negativo; (6) un año sin equity en `balance_history` forzaba el IC operativo en toda la serie; (7) las etiquetas del rating sintético estaban desplazadas una fila (7× salía "A+" en vez de AA); (8) una cita podía verificarse casando a caballo entre dos fuentes del corpus concatenado.
- **Causa raíz**: "ausente" se trataba como 0 o como "≤ 0", y "serie no vacía" como "dato del año"; el corpus de citas se unía con `"
"`.
- **Fix**: `missing_inputs` por año y nota "FCFF inputs absent for specific years"; `tax_rate_source` en `ValuationReport` y nota si es default; cadena de pretax con la variante de puesta en equivalencia; `terminal_nopat` con `MARGINAL_TAX_RATE`; intereses del mismo periodo que el EBIT en `suggest_wacc`; EV/EBIT solo con EBIT > 0; `force_operating` ignora equity `None` y `EQUITY_CONCEPTS` cae a la variante con NCI; tabla de ratings re-etiquetada; `validate_citations` verifica contra cada fuente por separado. Tests en `tests/test_h1_final_review.py`.
- **Commit**: ver `git log --grep "EC-91"`

### EC-92 — Las cíclicas se valoraban extrapolando el pico del ciclo
- **Síntoma** (run en vivo CALM, 2026-10-03): DCF $205/acción con precio $66,67 (+208 %). La base FCFF (mediana de 3 años) incluía FY2025, año de gripe aviar (margen operativo 36 % frente al 7,5 % típico); el crecimiento de la etapa 1 (16,6 %) salía del CAGR de ventas de 5 años, inflado por el mismo pico. El reverse DCF salía "barato" al compararse con ese 16,6 % y la convergencia ("3 de 4 métodos baratos") se usaba como argumento. Además aparecía la nota "DCF growth capped at 30%" cuando el valor usado era el tope por ventas (16,6 %): dos notas contradictorias.
- **Causa raíz**: la valoración asumía que los últimos 3–5 años son el poder de beneficio normal. Falso en empresas cuyo margen tiene picos muy por encima de su nivel típico (materias primas, acero, fertilizantes, alimentos básicos).
- **Fix**: detector determinista `valuation_math.assess_cyclicality` (≥5 años de margen operativo; cíclica si el mejor margen supera la mediana en ≥8 pp y es ≥1,75×; umbrales calibrados con márgenes reales de AAPL, AZO, BBW, PRDO, NUE, MOS y CALM). Si es cíclica: base FCFF = margen FCFF mediano de hasta 10 años × ventas actuales; EPV y valor terminal desde el EBIT mid-cycle (margen operativo mediano × ventas actuales); g1 = crecimiento terminal salvo override del usuario; reverse DCF comparado con el CAGR de ventas de toda la ventana. `ValuationReport.cyclicality`, línea "Cyclical: yes" en el informe, bloque `## CYCLICALITY (deterministic)` para el Value Expert y aviso en el checkpoint 2; el bloque de CAGR histórico del Expert avisa de que en una cíclica no es ancla (en el re-run el Expert seguía citando el 16,6 %). La nota del tope del 30 % solo aparece si el valor usado es ese tope. CALM añadido como 5.º arquetipo (MCE-25). Tests en `tests/test_valuation_cyclical.py`. Decisión: [ADR-034](docs/adr/ADR-034-cyclical-normalization.md).
- **Commit**: ver `git log --grep "EC-92"`

### EC-93 — La participación de directivos salía del LLM (y de la web) porque la tabla del proxy no llegaba
- **Síntoma** (runs en vivo AAPL/BBW/CALM tras H1): management 0 citas verificadas en 4 de 4 análisis. En CALM la única cita era una fila web ("9.93% | % of Shares Held by All Insider") atribuida al DEF 14A. Los contextos congelados de H1 muestran % inventados por el LLM (AAPL "0.000063%", MOS "approximately 5.57%"; la tabla real dice "*" — menos del 1 %).
- **Causa raíz**: (1) `fetch_def14a_text` recorta el proxy por títulos; si el título no coincide (CALM "Ownership of Voting Securities…", PRDO "Security Ownership of Principal Stockholders") cae en la última aparición de "beneficial ownership", que está DESPUÉS de la tabla. (2) `document.text()` trocea las tablas estrechas (AZO). (3) El extractor nativo de edgartools 5.59.1 devuelve vacío (`Filing.html()` → None en proxies inline XBRL) y convierte "*" en 0,5 %. (4) Verificado al probar: los tests de management/market/grafo descargaban EDGAR, Finnhub y Polygon REALES sin que nadie lo notara (suite de 38 s, pasaba con la red cortada porque los adaptadores degradan a []).
- **Fix**: `tools/proxy_ownership.py` lee la tabla por estructura HTML (`EdgarManagementAdapter.fetch_def14a_html`, vía `sgml().html()`); `insider_ownership_pct/_shares/_below_1pct` deterministas desde la fila "as a group"; el LLM ya no tiene ese campo y recibe la tabla limpia como SOURCE 1b (citable). Tablas multiclase o con varias columnas de % (PATH, GOOGL, BRK-B, NKE, NWSA, HEI, UHAL, SPG) → fila literal, nunca un % único (la última columna era el % de VOTO: PATH "85,2 %"). Fecha del proxy en el report y aviso si tiene >15 meses (EPD: 2022). Guard de red de los tests ampliado a `edgar.Company`, `finnhub.Client` y `polygon.RESTClient` + mocks por defecto de 8-K y Finnhub (suite 38 s → 7 s). Revisión adversarial en vivo sobre 33 emisores: 5 cifras equivocadas encontradas y corregidas con fixtures. Tests en `tests/test_proxy_ownership.py`, `tests/test_network_guard.py`. Decisión: [ADR-035](docs/adr/ADR-035-document-tables-deterministic.md).
- **Commit**: ver `git log --grep "EC-93"`

### EC-94 — Especialistas sin ninguna cita verificada pasaban como análisis normal
- **Síntoma** (4 análisis en vivo tras H1): management 0 verificadas en 4/4, filings 0 citas en 3/4, todos `stub=False`. Su narrativa llegaba al Value Expert sin etiqueta, el informe omitía la sección y no saltaba el tope de confianza. Apartados del 10-K recortados a 8–10K caracteres (15–20 % de cada sección en CALM); apartados vacíos pasaban en silencio. Rechazos de AAPL inflados ("0/6" eran 2 citas duplicadas). Una cita de AAPL ("Tim Cook has been CEO since August 24, 2011") no estaba en ninguna fuente: el prompt exigía cita para la antigüedad y el modelo la sacó de su entrenamiento.
- **Causa raíz**: la validación de citas (H1) medía pero nadie actuaba sobre el resultado; el validador no tenía falsos negativos (investigación adversarial: todo lo rechazado eran paráfrasis, uniones o invenciones).
- **Fix**: `llm_evidence_count` (excluye citas deterministas); esencial con 0 evidencia → DATA GAP, tope 0,6, "⚠️ UNVERIFIED" en el contexto y "no verified evidence" en el informe; reintento único en filings con los excerpts rechazados; `TENK_ITEM_MAX_CHARS = 25 000`; 10-K vacío → report con error sin LLM; antigüedad del CEO null si ninguna fuente tiene el año; citas no verificadas sin duplicados. Tests en `tests/test_evidence_gate.py`. Decisión: [ADR-036](docs/adr/ADR-036-evidence-gate.md).
- **Commit**: ver `git log --grep "EC-94"`

### EC-95 — Avisos de deserialización de LangGraph en cada análisis (y un fallo silencioso latente)
- **Síntoma** (runs de CALM): "Deserializing unregistered type due_diligence.schemas.reports.ValuationReport from checkpoint. This will be blocked in a future version" para cada report, en cada reanudación tras un checkpoint.
- **Causa raíz**: el checkpointer usaba el serializador por defecto (permisivo con aviso). Verificado en `langgraph/checkpoint/serde/jsonplus.py`: cuando se active el bloqueo, un tipo bloqueado vuelve como datos en bruto, sin excepción → el estado llegaría a los nodos como dicts.
- **Fix**: `graph/checkpointer.py` (`make_checkpointer`, `make_serializer`) con allowlist calculada de todos los modelos de `due_diligence.schemas`; estricto ya para cualquier otro tipo. CLI y scripts usan la fábrica. Tests en `tests/test_checkpointer.py`. Decisión: [ADR-037](docs/adr/ADR-037-strict-checkpoint-deserialization.md). Fase 5: MCE-32.
- **Commit**: ver `git log --grep "EC-95"`

### EC-96 — Cocientes y tendencias que mezclaban años cuando una serie tenía huecos
- **Síntoma** (revisión final de H1, menores #9/#10, y auditoría MCE-26): con un año ausente en una serie, (1) la tendencia del ROIC del Value Expert dividía entre posiciones y no entre años (FY2025 vs FY2023 contaba como 1 año: ΔROIC anual el doble); (2) el flag "HIGH CAPITAL RETURN" del Critic, (3) el criterio de moat "Buybacks ≥ 70 % of FCF" de competitive y (4) la línea "Capital returned … % of FCF" del Expert dividían recompras de un año entre FCF de otro.
- **Causa raíz**: `serie[0]` frente a `otra[0]` asume que ambas empiezan en el mismo ejercicio (mismo patrón que EC-82/EC-83).
- **Fix**: `utils.periods.value_at(series, period)`; los cuatro sitios usan el ejercicio del FCF / años fiscales reales; sin dato de ese año no se calcula el cociente (ni se pinta una línea vacía). Regla añadida a `agents/CLAUDE.md`. Test renombrado `test_parse_financials_cash_excludes_short_term_investments` (#12). Tests en `tests/test_period_alignment_minors.py`.
- **Decisiones**: `cash_series`/`equity_series` (#11) se mantienen — su consumo en H3-1 está documentado en `tests/test_schema_consumers.py`. El plan de H1 vive en `docs/superpowers/plans/` que `.gitignore` excluye por decisión del usuario (#14): no se versiona sin su indicación.
- **Commit**: ver `git log --grep "EC-96"`

### EC-97 — Bloque "GOVERNANCE RED FLAGS" para empresas sin ninguna bandera
- **Síntoma** (revisión adversarial del plan H2-1 sobre los 8 contextos congelados): todos tenían `governance_red_flags = "No governance red flags found in provided text."`, y el Value Expert, el Critic y el archiver mostraban el bloque `## GOVERNANCE RED FLAGS` como si hubiera banderas; la regla 23 se activaba sin motivo.
- **Causa raíz**: el prompt de filings se contradecía (línea 65: "set governance_red_flags to null"; línea 133: "explicitly state 'No governance red flags found…'") y los tres consumidores solo comprueban que el campo tenga texto.
- **Fix**: el prompt pide solo `null`; `filings._governance_or_none` convierte en el productor las frases de "nada" ("No governance red flags…", "Not available in provided sources", "None") en `None`, exigiendo que la frase ENTERA sea un "nada" (`fullmatch`): "Nonetheless, the SEC opened…" y "No governance red flags found; however, the auditor identified a material weakness…" se conservan (la versión por prefijo borraba esta última, detectado en la revisión final de H2-1). Tests en `tests/test_agents_1b.py`.
- **Commit**: ver `git log --grep "EC-97"`

### EC-98 — "Tendencia del ROIC a 8 años" cuando no hay datos en los últimos 4 (regresión de EC-96)
- **Síntoma** (dataset H2-1, AAPL): "ROIC trend interpretation: RISING (... over 8 years)". El ROIC de AAPL es n/m en FY2018-FY2024 (capital invertido < 5 % de activos, regla de T9) y la serie solo tiene FY2025, FY2017, FY2016.
- **Causa raíz**: el fix de EC-96 caía al siguiente punto disponible (`roic_series[1]`) cuando ningún año estaba dentro de la ventana de 4 años fiscales.
- **Fix**: sin ningún punto en 4 años → "n/a (no ROIC data within the last 4 fiscal years to compare)"; el check de la regla 22 no aplica. Test en `tests/test_period_alignment_minors.py`.
- **Commit**: ver `git log --grep "EC-98"`

### EC-99 — Modelos de razonamiento de OpenAI: 400 por `temperature` y truncación por tokens de razonamiento
- **Síntoma** (al probar gpt-6-luna para los especialistas, ADR-039): (1) toda llamada fallaba con `400 Unsupported value: 'temperature' does not support 0.2 with this model`; (2) con el parámetro corregido, filings de BBW dio `LLMTruncatedError` con `max_tokens=4096`.
- **Causa raíz**: (1) el cliente enviaba siempre `temperature=0.2` a OpenAI; las familias gpt-6 y gpt-5.6 solo aceptan la temperatura por defecto (mismo problema que ya resolvimos para Claude Opus 4.7+). (2) los tokens de razonamiento cuentan dentro de `max_completion_tokens`: luna gastó 7 655 tokens de salida en KBR frente a 1 376 de gpt-4o-mini.
- **Fix**: `_openai_accepts_temperature` en `llm/client.py` omite el parámetro para `gpt-6*`/`gpt-5.6*` en `structured()` y `complete()`; techo de 16 384 tokens en management, filings (y su reintento) y competitive (un techo no es coste). Precios oficiales de gpt-6-luna, gpt-5.6-luna y gpt-6.1-sol añadidos a `_MODEL_PRICING` (el precio por defecto falseaba el coste). MCE-26: el validador de tesis (`max_tokens=200`, gpt-4o-mini) anotado inline como excepción. Tests en `tests/test_llm_structured*.py`.
- **Commit**: ver `git log --grep "EC-99"`

### EC-100 — Minoristas con cierre en enero/febrero: edgartools pierde el último ejercicio
- **Síntoma** (diagnóstico de valoración 2026-10-11): HD y TGT fallan con `DataUnavailableError: latest 10-K period 2026-02-01 → FY2026 but statements' latest column is FY2025 (period misalignment)`.
- **Causa raíz**: en el 10-K de 2026, SEC etiqueta los tres ejercicios del filing (cierres 2024-01, 2025-02 y 2026-02) con el mismo `fy=2025` (el de la empresa: HD llama "fiscal 2025" al año que acaba en febrero de 2026). edgartools, para cierres que no son diciembre, etiqueta las columnas con ese tag (`enhanced_statement.py` ~1954) → colisión y se queda con el ejercicio anterior: la columna "FY2025" de HD contiene 159,5 B$ (cierre 2025-02-02) y el último año (164,7 B$) desaparece. Verificado en edgartools 5.59.1 y 5.61.1 (última en PyPI). Los raw facts sí lo tienen. El invariante de `build_raw` (EC-87) hizo su trabajo: sin él habríamos valorado con datos de un año antes.
- **Fix**: `restore_latest_fy_column` en `tools/xbrl_extract.py` reconstruye la columna del último 10-K desde raw facts emparejados por period_end (`EdgarFactsLookup`), solo en el caso exacto y comprobable: falta exactamente un año, raw facts tiene el revenue de ese año y el revenue de la columna anterior coincide (±0,5 %) con raw facts (si no, la colisión pudo dejar datos de otro año bajo esa etiqueta → se sigue fallando). Flag de procedencia en `data_quality_flags`. Verificado con datos reales: HD FY2026 (ingresos 164,7 B$, beneficio neto 14,2 B$) y TGT FY2026; BBW y AAPL sin cambios. MCE-26: la única entrada de estados multi-periodo de edgartools es `edgar.py` → `build_raw`. Tests en `tests/test_xbrl_extract.py`.
- **Commit**: ver `git log --grep "EC-100"`

### EC-101 — Recuento de acciones caducado en empresas multiclase (V, MA)
- **Síntoma** (diagnóstico de valoración 2026-10-11): V con 0,469 B acciones (≈1,97 B as-converted) y MA con 0,123 B (0,906 B diluidas) → DCF/precio 2,69 y 3,75: dos de las empresas más caras del mercado salían "baratas".
- **Causa raíz**: no era "solo cuenta la clase A". Las multiclase publican la portada (`dei:EntityCommonStockSharesOutstanding`) por clase; companyfacts descarta los hechos con dimensión, y el único valor sin dimensión que queda es de antes de separar por clases: V, 10-Q de enero de 2010; MA, octubre de 2010 (antes del split 10:1 de 2014). `facts.shares_outstanding` de edgartools devuelve "el más reciente" sin mirar la fecha. Además, V no tiene diluidas sin dimensión en companyfacts: su cifra económica (1.966 M, clases B y C convertidas a A) solo está en el XBRL del 10-K.
- **Fix**: `_filing_shares` (`tools/edgar.py`) con orden explícito: (1) portada de companyfacts solo si es posterior al cierre del último 10-K (la más actual en empresas de una clase); (2) si no, el XBRL del propio 10-K vía `share_facts_from_xbrl_df` + `resolve_filing_shares` (`tools/xbrl_extract.py`): una clase → portada; varias → diluida as-converted publicada por la empresa (total sin dimensión o, si no hay, la mayor por clase si es ≥ 95 % de la suma de portada; ya diluida, no se diluye dos veces); sin ella → suma de clases con aviso; (3) si nada es fresco → None con aviso y diluidas medias. Nunca un dato caducado. Aviso multiclase en `data_quality_flags`. Verificado con datos reales: V 2,69 → 0,64, MA 3,75 → 0,51, NKE 0,44 → 0,25 (no estaba señalada: companyfacts tenía 855 M acciones de julio de 2015, antes del split 2:1; las reales son 1.481 M), GOOGL 0,41 → 0,42 (antes se diluía dos veces); AAPL, AZO, BBW y PRDO idénticos a la línea base (la primera versión leía siempre del 10-K y movía las de una clase: la portada de un 10-Q posterior es más actual). MCE-26: era la única lectura de companyfacts sin fecha en `src/`. Tests en `tests/test_xbrl_extract.py` y `tests/test_edgar.py`.
- **Commit**: ver `git log --grep "EC-101"`

### EC-102 — Caja presentada junto con las inversiones a corto en una sola línea (TGT)
- **Síntoma** (verificación de EC-100): TGT con caja de 0,25 B$ (`facts:Cash`) cuando su balance muestra 5,49 B$.
- **Causa raíz**: TGT etiqueta la línea "Cash and cash equivalents" del balance con `CashCashEquivalentsAndShortTermInvestments`, que no estaba en `CASH_CONCEPTS`. `point_value` no la encontraba en la vista curada y caía a raw facts, donde `us-gaap:Cash` es una partida más estrecha de las notas. A la vez se sumaba un `ShortTermInvestments` de 4,61 B$ que es un componente de esa misma línea.
- **Fix**: `CASH_COMBINED` en la cadena de caja, antes que `Cash` (el concepto más estrecho); `resolve_sti` marca las inversiones a corto como "included in CashCashEquivalentsAndShortTermInvestments" (0) cuando la caja sale de la línea combinada, en el último año y en `balance_history`, para no restarlas dos veces (EC-81). Neutro para EV e IC: lo restado es caja + STI. Barrido de las 33 empresas antes/después: en caja solo cambia TGT (4,86 → 5,49 B$ de caja + STI). PG y CVX usan el fallback de caja con efectivo restringido, ya documentado y marcado en `source`. Tests en `tests/test_xbrl_extract.py`.
- **Commit**: ver `git log --grep "EC-102"`

### EC-103 — Beneficio neto vacío en empresas que solo publican `ProfitLoss` (MA, CAT)
- **Síntoma** (verificación de EC-101 y barrido de 33 empresas): `annual.net_income` vacío en MA y CAT (0 de 10 años) y con la mitad de los años en TGT. ROE, payout y todo lo que use el beneficio neto quedaba sin dato.
- **Causa raíz**: la cadena era solo `NetIncomeLoss`. Algunas empresas publican únicamente `ProfitLoss`, que incluye la parte de los minoritarios.
- **Fix**: `resolve_net_income` (`tools/xbrl_extract.py`), año a año sobre la rejilla del revenue (como EC-90): `NetIncomeLoss` → `ProfitLoss − NetIncomeLossAttributableToNoncontrollingInterest` (fórmula en `source`) → `ProfitLoss` etiquetado si no hay minoritarios publicados. Rejilla = años del revenue ∪ columnas del estado de resultados (la primera versión usaba solo la del revenue y PFE perdía 2 años; detectado con el barrido de 33 empresas). Verificado: MA 14,947 B$ y CAT 8,884 B$ (FY2025, 10 años), PFE 10 años. Limitación conocida: TGT no tiene `NetIncomeLoss` ni `ProfitLoss` en raw facts antes de FY2021 (6 de 10 años). MCE-26: era el único sitio donde se resolvía el beneficio neto. Tests en `tests/test_xbrl_extract.py`.
- **Commit**: ver `git log --grep "EC-103"`
