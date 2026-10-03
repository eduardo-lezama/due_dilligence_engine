# Notas didácticas — Bloque C (valoración FCFF). Borrador para DETAILED_EXPLANATION.md (T15)

## T10 — FCFF y equity bridge

**Concepto.** Hay dos formas coherentes de valorar una empresa con DCF:
- *Equity directo*: descontar el flujo para el accionista (FCFE, ya neto de intereses y de deuda nueva) al coste del equity (Ke).
- *Enterprise*: descontar el flujo para **todos** los proveedores de capital (FCFF, antes de intereses) al WACC → enterprise value (EV) → restar los derechos de los no-accionistas (deuda, minoritarios, preferentes) y sumar los activos no operativos (caja, inversiones) → equity → por acción.

El sistema hacía una mezcla: flujo apalancado (OCF − capex) descontado a un "WACC" y presentado como equity sin restar deuda. En una empresa apalancada eso sobrevalora (+18 % en el ejemplo A); en una con caja neta infravalora (−21 % en el ejemplo C).

**Por qué existe `valuation_math.py`.** Toda la aritmética (FCFF, bridge, acciones, DCF) en funciones puras, sin state ni LLM, testeadas a mano. `valuation.py` solo orquesta. Así DCF, EPV y reverse DCF usan exactamente la misma fontanería.

**Cómo funciona el FCFF.** `FCFF = OCF + int×(1−t) − int_income×(1−t) − capex − SBC − principal leasing financiero`.
- El OCF en US GAAP ya restó los intereses pagados → se suman netos de impuestos (desapalancar).
- El OCF incluye los intereses cobrados sobre la caja → se restan, porque la caja ya se suma en el bridge.
- SBC es salario pagado en acciones: coste real (Damodaran).
- Arrendamientos operativos (ASC 842): su pago ya reduce el OCF → no se restan otra vez en el bridge (sería −19,5 % en AZO).

**Valor terminal con reinversión.** Crecer exige reinvertir: g = tasa de reinversión × RONIC. Con RONIC = WACC (el crecimiento terminal no crea valor) el flujo terminal es NOPAT × (1+g) × (1 − g/WACC). Usar FCFF × (1+g) sin más supone crecer gratis.

**Fail loud.** WACC − g < 1 pp → `ValuationInputError`. Antes se recortaba en silencio.

**Ejemplo concreto (A).** OCF 260, capex 60, intereses 40, SBC 20, t 25 % → FCFF 210. A 9 % y g 0 con reinversión terminal: EV 2 403,7; menos deuda neta 450 → equity 1 953,7 → $195,37 por acción frente a $222,22 del método antiguo.
