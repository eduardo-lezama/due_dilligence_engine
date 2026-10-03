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
