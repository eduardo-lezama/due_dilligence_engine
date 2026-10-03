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
  2. Rule #18 en `_SYSTEM_PROMPT` del value_expert: cuando el bloque está presente, §4 MUST flag balance sheet stale, §5 MUST distinguir orgánico/inorgánico, §6 MUST cavetar EV multiples, §9 MUST nominar pending 10-Q como evento resolutor.
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
