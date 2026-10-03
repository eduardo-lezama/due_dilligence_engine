# ADR-033: FCFF a WACC real + puente EV → equity común a DCF, EPV y reverse DCF

**Status**: Accepted (2026-10-03, implemented in H1 Bloque C: T10–T13)
**Date**: 2026-10-03
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `valuation`, `fcff`, `wacc`, `equity-bridge`, `damodaran`, `greenwald`, `koller`
**Supersedes in part**: [ADR-029](ADR-029-three-stage-dcf-and-epv.md) (base del DCF, tipo impositivo, WACC advisory), [ADR-030](ADR-030-valuation-convergence-signaling.md) (umbral EPV, histórico de convergencia)

## Context

La auditoría de 2026-10-02 (H8, H12, §4.3) encontró que la valoración mezclaba planos:

- El DCF descontaba **FCF apalancado** (OCF − capex, ya neto de intereses) a una tasa llamada "WACC" que en realidad era un **coste de equity** CAPM, y trataba el resultado como **equity value** sin restar deuda (sobrevalora un 18 % una empresa apalancada; infravalora un 21 % una con caja neta).
- El EPV sí restaba deuda neta, así que DCF y EPV vivían en planos distintos y la convergencia de la ADR-030 comparaba manzanas con naranjas.
- El reverse DCF buscaba el **market cap** con un modelo de enterprise value.
- El upside del EPV caía a "EV vs market cap" cuando faltaba el precio.
- WACC ≤ g se recortaba en silencio a g = WACC − 0,5 pp (TV ≈ 200× FCF).
- El tipo impositivo `1 − NI/EBIT` metía los intereses (y la NCI) en el impuesto.

## Decisions

1. **FCFF = OCF + interest_expense×(1−t) − interest_income×(1−t) − capex − SBC − principal de leasing financiero.** El OCF US GAAP ya restó los intereses pagados (se suman netos) e incluye los cobrados sobre la caja (se restan: la caja se suma en el bridge; si no, doble cuenta, +13 % en un caso tipo PRDO). SBC es coste real (Damodaran) y por coherencia se usan acciones diluidas de hoy. El principal de leasing financiero es un capex equivalente que CFO − capex nunca descuenta.
2. **Arrendamientos operativos (ASC 842) NO se restan en el bridge.** El pago ya reduce OCF y EBIT; restar el pasivo cobraría dos veces (−19,5 % en un caso tipo AZO). Se muestra como memo. Los leasing financieros ya están en `total_debt`.
3. **Tipo impositivo canónico = `effective_tax_rate_multi` (Σtax/Σpretax, hasta 3 años, clamp [0, 0,5], 25 % por defecto)** — una sola función en `tools.xbrl_extract`, re-exportada por `valuation_math`. **`MARGINAL_TAX_RATE = 25 %`** para valor terminal, EPV y escudo fiscal del WACC (el efectivo converge al marginal en perpetuidad). La fórmula `1 − NI/(EBIT − int)` del borrador del plan queda descartada (revisión adversarial: falla con intereses cobrados).
4. **Acciones = shares_outstanding (portada) × (diluidas media / básicas media).** La media diluida a secas sobrecuenta en empresas con recompras fuertes (AZO ≈ +2,4 %).
5. **Equity de mercado = price × share_count; market EV = equity de mercado + net_claims.** Lo usan reverse DCF, EV/EBIT, EV/FCFF, earnings yield, `peers.py` y `critic.py`. `net_claims = deuda + minoritarios + preferentes − caja − STI − LTI` en una sola función.
6. **WACC − g < 1 pp → `ValuationInputError`** (fail loud). En la tabla de sensibilidad la celda inválida es `None` ("n/m"). El agente captura el error y devuelve `ValuationReport(stub=True, error=...)` para no tumbar el grafo; checkpoint 2 y la CLI re-preguntan.
7. **Convención de mitad de año disponible pero OFF por defecto en H1** (`ValuationInput.mid_year_convention=False`) por comparabilidad con los 51 reports históricos; factor `(1+WACC)^0.5` sobre el EV cuando se activa.
8. **Umbrales ADR-030:** DCF ±20 %, **EPV ±10 % simétrico** (antes < 0 = expensive), reverse ±3 pp, EV/EBIT 0,7×/1,3×.
9. **Histórico de convergencia = CAGR de ingresos** (`historical_cagr_5y`), no `near_term_growth` (circular). El reverse DCF muestra también la CAGR equivalente a 10 años del camino implícito.
10. **Upside siempre por acción frente a precio.** Sin precio → `None` + nota. Se elimina el fallback EV vs market cap.

### Cambios de la revisión adversarial incorporados

- **Valor terminal con reinversión** (Damodaran/Koller): flujo terminal = NOPAT_T(1+g)(1 − g/RONIC), RONIC = WACC por defecto. Sin esto el TV se sobrevalora ~25 % con g = 2,5 %.
- **Base del DCF normalizada**: mediana del margen FCFF de hasta 3 ejercicios × ingresos del último (like-with-like con el EPV, que usa mediana de EBIT).
- **WACC real** = wE·Ke + wD·Kd·(1−t_marginal) con pesos a mercado; **Kd por rating sintético** (cobertura EBIT/intereses → spread sobre Rf, tabla Damodaran); **prima de tamaño visible pero OFF por defecto** (con β por industria en H3 se contaría dos veces).
- **Capitalización del EV desde el cierre fiscal hasta la fecha de valoración**: `EV × (1+WACC)^(días/365)` (10 meses a 9 % ≈ +7,4 %).
- **EPV**: mediana de EBIT incluyendo años negativos (None si ≤ 0); `growth_premium = 1 − EPV/precio` (Greenwald).
- **Market EV ≤ 0** → etiqueta "net-net" y múltiplos EV "n/m".
- Una sola fuente de defaults del checkpoint 2 (`CHECKPOINT2_DEFAULTS`).

## Ejemplos numéricos de referencia ($M)

**A. Apalancada.** OCF 260, capex 60, intereses 40, SBC 20, EBIT 300, deuda 500, caja 50, 10M acciones, precio 150, WACC 9 %, g 0, t 25 %.
FCFF = 260 + 30 − 60 − 20 = 210. Explícito (10 años): 210 × 6,417658 = 1 347,71. Terminal con reinversión: NOPAT 225 / 0,09 = 2 500 → PV 2 500 × 0,4224108 = 1 056,03. EV = 2 403,74. Net claims 450 → equity 1 953,74 → **$195,37** (+30,2 %). Sin reinversión terminal sería $188,33; el método antiguo daba $222,22 (+48,1 %). EPV: 225/0,09 = 2 500 − 450 = 2 050 → $205,00. Market EV = 1 500 + 450 = 1 950; EV/EBIT 6,5×; EV/FCFF 9,29×.

**B. Tipo AZO** (equity contable −3 400). OCF 3 000, capex 800, int 400, SBC 100, t 25 % → FCFF 2 400. EV (perpetuidad 9 %) 26 666,67. Deuda 9 000, caja 300 → net claims 8 700 → equity 17 966,67. Acciones 16,8M × 17,6/17,2 = 17,1907M → **$1 045,14**. Restar leases operativos (3 500) sería −19,5 % (doble cuenta evitada).

**C. Tipo PRDO** (caja neta). OCF 150, capex 5, int 0, SBC 10, interest income 31,5, caja 400, STI 300, LTI 100 (net claims −800), 65M acciones. FCFF = 150 − 5 − 10 − 23,625 = 111,375. EV 1 237,5 → equity 2 037,5 → **$31,35**. Método antiguo: $24,79 (−21 %). Sin restar interest income: $35,38 (+13 % doble cuenta).

## Architecture

```mermaid
flowchart LR
  F[FinancialsReport XBRL] --> FCFF[fcff_breakdown<br/>OCF + int·(1−t) − int_inc·(1−t) − capex − SBC − FL principal]
  F --> T[effective_tax_rate_multi]
  T --> FCFF
  M[MarketReport price, mkt cap] --> W[suggest_wacc<br/>Ke CAPM + Kd synthetic rating]
  F --> W
  W --> CP2[checkpoint 2<br/>CHECKPOINT2_DEFAULTS + validate_dcf_rates]
  CP2 --> DCF[dcf_components<br/>3-stage + TV con reinversión]
  FCFF --> DCF
  DCF --> EV[EV × (1+WACC)^(días/365)]
  EV --> B[equity_bridge<br/>− deuda − NCI − pref + caja + STI + LTI]
  B --> PS[per_share vs price]
  F --> EPV[EPV = mediana EBIT·(1−t_m)/WACC]
  EPV --> B
  M --> MEV[market EV = price·shares + net_claims]
  MEV --> R[reverse DCF / EV multiples / peers]
```

## Consequences

- DCF, EPV y reverse DCF comparan en el mismo plano; la convergencia de la ADR-030 vuelve a tener sentido.
- Cada componente del bridge y del FCFF es visible en el informe y en el contexto del Value Expert.
- Cambian los valores por acción de todos los tickers respecto a los reports históricos (esperado y documentado en EC-72..EC-77).
- Pendiente (H3): pensiones en el bridge, capitalizar leases operativos al estilo Damodaran, β por industria, rf dinámica, tabla de spreads actualizada automáticamente.

## Related

- ADR-005 (zero hallucination numérica), ADR-029, ADR-030.
- Plan: Horizonte 1 — Bloque C (T10–T13).

## Implementation notes (H1 Bloque C)

- `valuation_math.py` holds all pure arithmetic (FCFF, bridge, share count, 3-stage DCF with terminal reinvestment, Kd by synthetic rating, WACC, `describe_wacc`); `valuation.py` orchestrates. The canonical tax functions are re-exported from `tools.xbrl_extract`, never redefined.
- The synthetic-rating spread table is the plan's table, marked in code "VERIFY against Damodaran ratings.htm (Jan 2026)" — it was not verified online at implementation time.
- One capitalization factor `(1+WACC)^(days since fiscal year-end / 365)` is applied to the DCF EV, the EPV and (inverted) to the reverse-DCF target, so the three methods are dated consistently.
- The sensitivity table runs the headline path (terminal reinvestment, capitalization, bridge, diluted shares): its centre cell equals the reported DCF value per share (example A: $195.37).
- `ValuationReport(stub=True, error="ValuationInputError: ...")` is the fail-loud output when inputs are invalid; archiver, CLI and Value Expert context render it as "valuation not computed".
- Single source of checkpoint-2 defaults: `checkpoint.CHECKPOINT2_DEFAULTS`; the CLI reads the `interrupt()` payload (`result["__interrupt__"][0].value`, LangGraph 1.1.8) and the node re-interrupts with `error` when WACC − g < 1 pp.
- Known limitation: near-term growth (g1) is still derived from the levered FCF CAGR / revenue CAGR (ADR-022/027); an FCFF/NOPAT-based growth anchor is H3-4.
