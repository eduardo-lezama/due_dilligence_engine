# ADR-022: Small-Cap Display y Metodología DCF Near-Term Growth

**Status**: Accepted  
**Date**: 2026-05-10  
**Context**: Análisis de BBW y JBSS (small caps, mkt cap $300M–$1B) reveló tres problemas sistémicos detectados mediante auditoría con modelo externo.

---

## Contexto

Tras correr el sistema contra BBW (Build-A-Bear, mkt cap ~$300M, FCF ~$40M) y JBSS (John B. Sanfilippo, revenue $1.1B), se detectaron tres bugs que invalidan partes del output para small/mid caps:

1. **EC-14 — Formatter**: todas las cifras se dividían por 1e9 antes de enviarse al LLM. Para FCF=$39.5M, el LLM recibía `$0.0B` y lo reproducía en su análisis, invalidando §4 Financial Quality y el VERDICT rationale.

2. **EC-15 — DCF growth**: el near-term growth del DCF se calculaba como CAGR del FCF histórico. El FCF es más volátil que el revenue (working capital, timing de capex), lo que produce growth assumptions absurdos para small caps con FCF ruidoso (BBW: 25.6% near-term growth para un retailer que crece ~7% en revenue).

3. **EC-16 — Contradicción DCF**: el forward DCF y el reverse DCF pueden contradecirse flagrantemente (BBW: forward asume 25.6% growth generando +255% upside, pero el reverse dice que el precio actual implica solo 1.6% growth — spread de 24pp) sin que el sistema lo detecte.

---

## Decisiones

### 1. Formatter adaptativo `_fmt_usd(value)`

**Decisión**: crear un helper que elija la unidad automáticamente:
- `>= $1B` → `$X.XB`
- `>= $1M` → `$XXXM`
- `>= $1k` → `$XXk`
- menor → `$XX`

Definido en `value_expert.py` y `archiver.py` (duplicado deliberado — YAGNI, sin `utils.py` todavía).

**Alternativa rechazada**: formatear siempre en billones con más decimales (`$0.04B`). Rechazada porque los números seguirían siendo ilegibles para el LLM y para el usuario humano.

### 2. DCF near-term growth: `min(FCF_CAGR, revenue_CAGR)`

**Decisión**: cuando `historical_cagr_5y` (revenue CAGR) está disponible en el estado, el near-term growth del DCF es `min(FCF_CAGR, revenue_CAGR)`.

**Razonamiento**: el FCF no puede crecer sostenidamente más rápido que el revenue sin compresión de márgenes. El revenue CAGR (calculado sobre 3–5 años de XBRL) es más estable que el FCF CAGR, que oscila por working capital y timing de capex. Usar `min()` es conservador y apropiado para value investing.

**Jerarquía completa** (sin cambios en el override del usuario):
1. `revenue_growth_yr1_pct` del Checkpoint 2 (usuario sabe lo que hace)
2. `min(FCF_CAGR, historical_cagr_5y)` (conservador, defensivo)
3. `historical_cagr_5y` si no hay FCF CAGR
4. 7% por defecto

**Alternativa rechazada**: usar siempre revenue CAGR. Rechazada porque si el FCF CAGR es menor que el revenue CAGR (empresa con márgenes en compresión), queremos usar el FCF CAGR más pesimista.

### 3. Flag de contradicción DCF

**Decisión**: si `near_term_growth - reverse_dcf_implied_growth > 5pp` y el usuario no ha hecho override, añadir advertencia a `dcf_reliability_note`:

> ⚠️ DCF contradiction: forward DCF assumes X% near-term growth but market prices only Y% (spread: Zpp). The DCF upside is conditional on growth materially above what the market currently implies — not a margin of safety.

**Threshold elegido**: 5pp. Por encima de ese spread, el upside DCF es mayoritariamente un artefacto de assumptions no validadas por el precio de mercado.

**Guard**: no se activa si el usuario ha provisto `revenue_growth_yr1_pct` (él es consciente del spread).

---

## Consecuencias

- Small caps (mkt cap < $500M) con FCF < $100M producen ahora output legible y coherente.
- El DCF de retailers maduros ya no produce uprises de +250% por FCF CAGR ruidoso.
- Las contradicciones flagrantes forward/reverse DCF son visibles en el report antes de que el analista humano las detecte.
- El formatter está duplicado en dos módulos — si se añade un tercer uso, extraer a `due_diligence/utils.py`.
- La lógica de growth en `valuation.py` lee `state["historical_cagr_5y"]` que produce `financials_node` — acoplamiento suave por estado compartido (LangGraph pattern estándar).
