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
