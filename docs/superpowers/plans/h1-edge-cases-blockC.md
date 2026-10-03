# EDGE_CASES del Bloque C (EC-72 a EC-77) — borrador para consolidar en EDGE_CASES.md (T15)

Formato: síntoma → causa raíz → fix → commit.

## EC-76 — WACC ≤ g recortado en silencio
- **Síntoma:** con WACC 3 % y g 2,5 % el DCF devolvía valores absurdos (TV ≈ 200× FCF) sin aviso; con WACC = g, el código recortaba g a WACC − 0,5 pp.
- **Causa raíz:** `_three_stage_dcf` "arreglaba" el input inválido (`terminal_growth = wacc - 0.005`) en lugar de fallar.
- **Fix:** `valuation_math.validate_dcf_rates` lanza `ValuationInputError` si WACC ≤ 0 o WACC − g < 1 pp (con `round(…, 9)` para tolerar coma flotante); `_sensitivity_table` convierte las celdas inválidas en `None`; el agente (T12a) captura y devuelve `ValuationReport(stub=True, error=...)`; checkpoint 2 y la CLI re-preguntan (T11).
- **Commit:** feat(h1-T10) / feat(h1-T11) / feat(h1-T12a).

## EC-77 — Sesgos de inputs del FCF y del tipo impositivo
- **Síntoma:** FCF sobreestimado (SBC sumado como no-caja), caja contada dos veces (intereses cobrados dentro del OCF + caja sumada en el bridge), tipo impositivo sobreestimado (EBIT 300, int 40, NI 195 → 35 % en vez de 25 %); EPV inflado en cíclicas por descartar años con EBIT negativo.
- **Causa raíz:** `t = 1 − NI/EBIT` mete intereses y NCI en el impuesto; el FCF apalancado no restaba SBC ni intereses cobrados; `_ebit_median` filtraba `value > 0`.
- **Fix:** `fcff_breakdown` (SBC como coste, interest income neto restado, principal de leasing financiero restado); tipo canónico `effective_tax_rate_multi` y `MARGINAL_TAX_RATE` para perpetuidades; `_ebit_median` incluye negativos y devuelve None si la mediana ≤ 0.
- **Commit:** feat(h1-T10).

## EC-72 — DCF descontaba FCF apalancado al WACC sin bridge
- **Síntoma:** valor por acción del DCF +18 % en una empresa apalancada (ejemplo A: $222,22 vs $195,37 correcto) y −21 % en una con caja neta (ejemplo C: $24,79 vs $31,35); `_mv_val(financials.diluted_shares)` sobre una lista habría dado `AttributeError` en cada run.
- **Causa raíz:** `FCF = OCF − capex` (ya neto de intereses) descontado a una tasa llamada WACC y presentado como equity sin restar deuda ni sumar caja/STI/LTI; acciones = portada sin dilución.
- **Fix:** `valuation_agent` → FCFF normalizado (mediana del margen 3 años) → `dcf_components` con valor terminal con reinversión → EV capitalizado a la fecha de valoración → `equity_bridge` (deuda, NCI, preferentes, caja, STI, LTI + participaciones) → `resolve_share_count` (portada × diluidas/básicas) → upside por acción. `ValuationInputError` capturado → `ValuationReport(stub=True, error=...)`. Test de integración `parse_financials → valuation_agent` con STI > 0.
- **Commit:** feat(h1-T12a).

## EC-73 — EPV upside comparaba EV con market cap sin precio
- **Síntoma:** sin precio (o con shares ausentes) el upside del EPV se calculaba como (EPV_enterprise − market cap)/market cap: EV contra equity, sesgado por toda la deuda neta. El EPV además restaba solo deuda − caja (sin STI/LTI/NCI/preferentes) y usaba `1 − NI/EBIT` como tipo.
- **Causa raíz:** fallback "indicativo" que mezclaba planos; bridge del EPV distinto del (inexistente) del DCF.
- **Fix:** `_compute_epv(ebit_series, wacc, net_claims, shares, price_current, mid_year, *, capitalization)` → mismo `net_claims` que el DCF, tipo marginal, misma capitalización a fecha de valoración, upside solo por acción (None + nota sin precio), `epv_growth_premium = 1 − EPV/sh ÷ precio` (Greenwald).
- **Commit:** feat(h1-T12b).

## EC-74 — Reverse DCF buscaba el market cap con un modelo de EV
- **Síntoma:** el growth implícito salía infraestimado en empresas apalancadas y sobreestimado con caja neta; los múltiplos EV usaban `market_cap + deuda − caja` (STI ignorada) y EV/FCF apalancado; peers con otra definición de EV.
- **Causa raíz:** target equity (market cap) frente a un DCF que produce EV; cada consumidor tenía su "net debt".
- **Fix:** target = market EV (precio × acciones + `net_claims`), deshecha la capitalización, misma función `dcf_components` (incluida la reinversión terminal y mid-year) que el DCF directo; None si la solución toca los bordes de búsqueda o market EV ≤ 0 ("net-net", múltiplos n/m). EV/EBIT, EV/FCFF, earnings yield, P/E (equity de mercado / NI), `peers.py` y `critic.py` usan `net_claims`.
- **Commit:** feat(h1-T12b).

## EC-75 — "Histórico" de convergencia era near_term_growth (circular)
- **Síntoma:** la fila Reverse DCF de la convergencia comparaba el growth implícito con el mismo FCF CAGR que alimentaba el DCF directo; además comparaba un g1 de stage 1 (con fade posterior) contra una CAGR plana, y el EPV marcaba "expensive" con cualquier upside < 0 (asimétrico). La sensibilidad mostraba "equity" total en USD B sin bridge.
- **Causa raíz:** `historical_cagr_for_conv = near_term_growth`; umbral EPV `< 0`; `_sensitivity_table` sobre `_three_stage_dcf` sin bridge ni acciones.
- **Fix:** histórico = `historical_cagr_5y` (CAGR de ingresos); clasificación del reverse sobre la CAGR equivalente a 10 años del camino implícito (`_ten_year_equivalent_cagr`, `reverse_dcf_10y_equivalent`); banda EPV ±10 % simétrica; sensibilidad en USD/share por el mismo camino que el titular (reinversión terminal, capitalización, bridge), celdas inválidas `None`, `sensitivity_unit="USD/share"`; ADR-030 con la tabla de umbrales corregida.
- **Commit:** feat(h1-T12c).

## Conocidos — pendientes (Bloque C)
- Inputs FCFF ausentes en los fixtures legacy (sin interest/SBC/interest income): el informe lo avisa (nota de reliability) pero el FCFF de esos runs es OCF − capex. Re-fetch de fixtures en T14.
- g1 (near-term growth) sigue saliendo del CAGR del FCF apalancado / ingresos (ADR-022/027): con capex creciente puede dar g1 negativo (AZO −11 %) y arrastrar el DCF; ancla FCFF/NOPAT = H3-4.
- Tabla de spreads del rating sintético sin verificar contra Damodaran ratings.htm (Jan 2026).
- LTI puede incluir participaciones operativas; pensiones fuera del bridge (H3-6b); leases operativos no capitalizados (H3).
- Kd con deuda contable como proxy del valor de mercado.
