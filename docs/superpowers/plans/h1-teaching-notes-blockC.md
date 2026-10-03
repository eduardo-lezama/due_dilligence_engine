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

## T11 — WACC real frente a coste de equity

**Concepto.** El "WACC sugerido" antiguo era Rf + β·ERP (+ prima de tamaño): un **coste de equity** (Ke). El WACC real pondera el coste de cada proveedor de capital por su peso a valor de mercado: `WACC = E/(D+E)·Ke + D/(D+E)·Kd·(1−t)`.

**Por qué importa.** Descontar FCFF (flujo para todos) a Ke (rendimiento exigido solo por el accionista) infravalora las empresas apalancadas: la deuda es más barata y además deduce impuestos (escudo fiscal).

**Cómo funciona.**
- **E** = market cap (nunca el book equity: AZO lo tiene negativo). **D** = deuda contable del último año (proxy del valor de mercado; sin leases operativos, coherente con el bridge).
- **Kd por rating sintético** (Damodaran): cobertura EBIT/intereses → rating → spread sobre Rf. Refleja el coste MARGINAL de endeudarse hoy, no el cupón histórico. Tabla marcada "verify against Damodaran ratings.htm (Jan 2026)".
- **t marginal 25 %** para el escudo fiscal (perpetuidad).
- **Prima de tamaño visible pero OFF**: Damodaran la rechaza; con β por industria (H3) se contaría dos veces.

**Checkpoint 2.** Un solo `CHECKPOINT2_DEFAULTS` para nodo, CLI y agente. El nodo construye el payload (`_build_checkpoint2_summary`) con el WACC real como default y su desglose; la CLI lo lee de `result["__interrupt__"][0].value` (LangGraph 1.1.8) en lugar de recalcular. El nodo también valida WACC − g ≥ 1 pp y vuelve a interrumpir con `error` si no: LangGraph empareja cada `interrupt()` de un nodo con su resume por orden, así que el bucle es seguro (test con grafo real).

**Ejemplo.** AZO-like: Ke 9,2 %, cobertura 9,0× → Kd 4,8 %, E 60B / D 9B → WACC = 0,8696·9,2 % + 0,1304·3,6 % = 8,47 %.

## T12a — DCF sobre FCFF → EV → bridge → por acción

**Qué cambia.** El nodo `valuation_agent` deja de descontar FCF apalancado y lo trata como equity. Ahora: tipo canónico (Σtax/Σpretax alineado por periodo) → FCFF por año → base normalizada (mediana del margen FCFF de 3 años × ingresos del último; like-with-like con el EPV) → DCF 3-stage con valor terminal con reinversión → EV × (1+WACC)^(días desde el cierre/365) → bridge → acciones diluidas → upside frente al PRECIO.

**Fail loud sin tumbar el grafo.** Cualquier `ValuationInputError` (WACC − g < 1 pp, signo de capex negativo...) se captura en el nodo y sale como `ValuationReport(stub=True, error=...)`: el archiver y la síntesis lo ven como hueco de datos visible.

**Defaults.** Sin WACC del usuario se usa el WACC real sugerido (con market cap) o 9,0 (sin él). `mid_year_convention` llega cableado (False por defecto).

**Ejemplo A.** FCFF 210, NOPAT 225, WACC 9 %, g 0: EV 2 403,7 − net claims 450 = 1 953,7 → $195,37 (+30,2 % vs 150).

## T12b — EPV, reverse DCF, múltiplos y peers en el mismo bridge

**Idea.** Un solo plano para todo: el EPV (Greenwald, valor sin crecimiento) es un EV, así que pasa por el MISMO bridge que el DCF; el reverse DCF pregunta qué crecimiento hace que el DCF iguale el **EV de mercado** (no el market cap); los múltiplos EV usan ese mismo EV de mercado; los peers y el Critic usan la misma función `net_claims`.

**Growth premium (Greenwald).** `1 − EPV/precio`: qué fracción del precio paga crecimiento. Si es alta y no hay moat (ROIC ≤ WACC), el crecimiento no crea valor y el precio está pagando algo que no existe.

**Net-net.** Si la caja neta supera al equity de mercado, el EV de mercado es ≤ 0: los múltiplos EV no significan nada (n/m) y el reverse DCF no tiene solución.

**Ejemplo A.** EPV = 300 × 0,75 / 9 % = 2 500 − 450 = 2 050 → $205 (+36,7 %). Market EV = 1 500 + 450 = 1 950 → EV/EBIT 6,5×, EV/FCFF 9,29×.

## T12c — Convergencia, umbrales y sensibilidad por acción

**Circularidad eliminada.** El "histórico" con el que se compara el crecimiento implícito por el mercado es ahora la CAGR de **ingresos**, no el mismo FCF CAGR que alimenta el DCF.

**Comparar peras con peras.** El reverse DCF devuelve un g1 de stage 1 que luego hace fade hasta g. Un 8 % con 5 + 5 años equivale a ≈ 6,33 % medio a 10 años: eso es lo que se compara con la CAGR histórica.

**Sensibilidad por acción.** Cada celda es WACC × g1 → EV (mismo modelo) → − net claims → / acciones; la celda central coincide con el valor por acción del informe ($195,37 en el ejemplo A), así el lector puede leer directamente "a 11 % de WACC vale X por acción".

## T13 — El informe muestra el bridge

**Qué ve ahora el lector.** El archiver añade "### Enterprise → Equity Bridge" (EV del DCF − deuda − NCI − preferentes + caja + STI + LTI = equity ÷ acciones diluidas = valor por acción, con los leases operativos como memo ASC 842), el pie con base FCFF, tipo, mid-year y % del valor que viene del terminal, la sensibilidad en USD/share ("n/m" en celdas inválidas, "USD B (legacy)" en reports viejos) y una sección "## Data quality flags". Si la valoración falló con un error tipado, se muestra el error.

**Contexto del Value Expert.** Recibe el FCFF con componentes, el desglose del WACC, el bridge, la cuota del terminal (si > 75 %: "§6 must flag terminal-value dependence"), el growth premium del EPV con su lectura Greenwald y el bloque "## DATA QUALITY FLAGS (deterministic)". Todo upside es "vs price"; la regla 21(d) del prompt dice ahora "EPV per share > price".

**CLI.** `_show_valuation` muestra EV (DCF), net claims, equity tras el bridge y valor por acción; si hay error, panel rojo.
