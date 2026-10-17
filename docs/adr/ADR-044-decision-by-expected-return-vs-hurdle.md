# ADR-044: Decisión por rentabilidad esperada frente a un listón explícito

**Status**: Accepted (2026-10-17, bloque B)
**Date**: 2026-10-17
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `valuation`, `verdict`, `margin-of-safety`, `hurdle-rate`
**Supersedes (parcialmente)**: [ADR-041](ADR-041-verdict-taxonomy.md): margen por tipo de negocio y ancla mín(DCF, EPV)
**Related**: [ADR-029](ADR-029-three-stage-dcf-and-epv.md), [ADR-033](ADR-033-fcff-wacc-equity-bridge.md), [ADR-034](ADR-034-cyclical-normalization.md), [ADR-043](ADR-043-block-a-robust-valuation-and-quality-signals.md), `docs/audits/2026-10-17-big-picture-review.md`

## Context

La revisión de conjunto del 2026-10-17 encontró varios problemas en la decisión.

- **El BUY era casi inalcanzable.**
  - El precio de entrada era mín(DCF, EPV) × (1 − 30/50/60 %).
  - Cada capa ya era conservadora: β = 1, prima de riesgo histórica sobre un Rf actual, RONIC = WACC, EPV sobre EBIT antiguo y el mínimo de dos métodos.
  - Ninguna de 30 empresas era comprable, ni con el precio un 40 % más bajo.
- **La banda "DCF/precio 0,5–0,9"** con la que se ajustó el bloque A medía sobre todo el tipo de descuento: calibraba contra el propio precio.
- **El crecimiento implícito del reverse DCF** exageraba lo que "exige" el precio de un negocio con ventaja duradera, porque con RONIC = WACC el crecimiento posterior al año 10 no vale nada.
- **Eduardo preguntó qué recomendaría un inversor value** para un listón "ni laxo ni restrictivo".

## Decisions

1. **Listón explícito = max(10 %, Rf + ERP)**, configurable con `HURDLE_RATE_PCT`.
   - El 10 % es la rentabilidad nominal histórica de la bolsa de EE. UU.: si una acción rinde menos, compensa más un fondo indexado.
   - Nunca baja de lo que el modelo exige al mercado en conjunto.
   - Hoy: 10,1 % (Rf 5,10 % + 5 %).
2. **Rentabilidad implícita del accionista.** Es la Ke que iguala el valor por acción al precio de hoy.
   - Se calcula por el mismo camino que el titular: DCF, capitalización, puente y acciones.
   - WACC = wE·Ke + wD·Kd(1−t), con pesos de mercado.
   - RONIC del terminal = WACC de mercado: una propiedad económica de la empresa, no lo que exige el inversor.
   - Se calcula para los escenarios pesimista, base y optimista de A3.
3. **Precio de entrada** = el máximo precio con el que se cumplen las dos condiciones:
   - el caso base rinde ≥ listón;
   - el pesimista rinde ≥ Rf.

   Es el margen de seguridad de Graham expresado en rentabilidad y medido con la historia de la propia empresa: una cíclica o un negocio de márgenes dispersos tiene un pesimista peor y una entrada más baja, sin tablas por tipo de negocio.

   **Casos especiales:**
   - **Sin escenarios:** la entrada solo prueba el caso base y lo dice.
   - **Pesimista sin valor ni al Rf:** no hay entrada, y se explica por qué.
4. **`verdict_policy`.**
   - **Lo que desaparece:** `REQUIRED_MOS` y `intrinsic_anchor`.
   - **BUY:** solo si el precio ≤ entrada. Si no, el código lo baja a WATCHLIST.
   - **Tamaño:** 5 % de base; +2,5 % si incluso el pesimista rinde el listón; −2,5 % si el precio supera el EPV; máximo 10 %.
   - **Tipo de negocio:** pasa a ser descriptivo. Se mantiene el endurecimiento: una cíclica no es "average" y un negocio sin moat no es "compounder".
   - **Nuevas en SQLite:** `hurdle_pct`, `implied_return_pct` y `bear_return_pct`.
   - **`METHODOLOGY_VERSION`:** "H2b".
5. **Coherencia de la valoración**, cambios aplicados a todas las empresas:
   - **EPV y NOPAT terminal** sobre el margen EBIT mediano (≤ 5 años) × ventas actuales, como la base FCFF. Antes era la mediana en dólares: MSFT −26 %.
   - **Transición en el fade.** El flujo pasa gradualmente de FCFF a NOPAT·(1 − g/RONIC), así que no hay escalón en el año N+1. Antes el flujo caía un 15–40 % de golpe.
   - **Ciclicidad:**
     - mediana del margen ≤ 0 → "en pérdidas", no cíclica (PATH, PLUG);
     - "no evaluada" se ve en la decisión.

     Se probó y se descartó un test de valles: marcaba BBW, PRDO y MSFT y seguía sin detectar XOM, DE ni CAT.
   - **Ruptura estructural:** si la tendencia de 10 años y el CAGR de 5 años difieren > 4 pp, se avisa sin cambiar g. Cambiarlo reintroduciría el rebote post-COVID que corrigió A1.
   - **Escenarios de las cíclicas:** el pesimista y el optimista salen de medias móviles de 3 años del margen FCFF; el base sigue siendo la mediana de los años sueltos, la misma del titular.
     - **Por qué:** el valor terminal es una perpetuidad, y perpetuar el margen de un año valle supone que el ciclo no vuelve.
     - **Caso medido:** CALM salía con un pesimista de −2,5 % para siempre, equity casi nulo con 0,9 B$ de caja neta y ningún precio de entrada.
6. **Prompt del Value Expert alineado:**
   - **§6** empieza por la rentabilidad frente al listón.
   - **Regla 21(c)** ya no prohíbe el BUY cuando los métodos divergen. Exige que el crecimiento que se paga por encima del EPV esté respaldado por el moat y por el ROIC incremental.
   - **Regla 4:** los rompe-tesis comprueban el negocio, no la posición en el ciclo.
   - **Regla 25 (nueva):** los scores contables en zona de alarma van a §4 y §7.
7. **Evals:**
   - **Checks retirados:** `buy_floor` (> 20 % de upside) y `rule21_divergence_no_buy`, porque volvían a apilar márgenes.
   - **Check numérico del BUY:** `buy_mos_floor` (precio ≤ entrada) es el único.
   - **Prompt pendiente de la puerta:** su huella queda en `tests/evals/baselines/pending_gate.json`. Eduardo decidió pasar la puerta una sola vez, en B4.

## Alternatives considered

- **Mantener MOS 30/50/60 y quitar solo el mín(DCF, EPV).** Seguía apilando margen sobre un DCF conservador, y dejaba en manos del LLM una palanca numérica (la clase) con riesgo de sicofancia.
- **Listón por tipo de negocio** (10 / 12 / 14 %). Tres números más sin verdad contra la que medirlos. El pesimista de la propia empresa ya diferencia.
- **Listón = Rf + ERP implícita** (~9,3 % hoy). Se mueve con el mercado: en burbujas bajaría justo cuando conviene exigir más.
- **RONIC terminal = ROIC histórico** para negocios con moat. Reintroduce un juicio (¿quién tiene moat?). Queda en MCE-39.
- **Ajustar hasta caer en una banda de valor/precio.** Circular, retirado.

## Consequences

- **Una cifra principal:** "a este precio rinde X % al año si cumple su historia; tu listón es Y %". El margen de seguridad deja de ser una tabla y pasa a ser el caso pesimista de la empresa.
- **Sin BUY para la calidad a estos precios:** con el mercado de octubre de 2026, las empresas de calidad rinden un 4–8 % implícito. Es WATCHLIST, y es la respuesta correcta para un listón del 10 %.
- **El criterio de la investigación** de "60 % de calidad comprable con −40 %" no se alcanza por nivel de mercado, no por el modelo. Queda como alarma.
- **Lo que hace el código:**
  - El DCF de las compounders baja ~5–10 % por la transición del fade y el NOPAT normalizado.
  - El EPV de las que crecen sube.
- **El dataset de evals queda desfasado** hasta B4.
- **Revisión de código independiente:** 9 hallazgos, todos corregidos y con test.
  - **Alto:** con deuda o un g terminal alto, el suelo de Ke superaba a Rf, el pesimista dejaba de evaluarse y el informe decía "no bear scenario". Ahora se evalúa al mayor de los dos tipos.
  - **Medios:**
    - el extractor de 10-K cortaba en prosa que empieza línea ("Item 1A of this report…"); ahora exige título;
    - las reglas 21(b) y 21(d) del prompt aún llamaban BUY de máxima convicción a EPV > precio.
  - **Bajos:**
    - nota de "metodología antigua" con `methodology_version`;
    - filas con `run_id` NULL en "cambios desde el último análisis";
    - test del suavizado reforzado;
    - historia en pérdidas sin EBIT normalizado negativo;
    - etiqueta de la deuda neta;
    - CI con `--locked` y permisos de solo lectura.

## Resultados (diagnóstico de 33 empresas, sin LLM, 2026-10-17)

Datos en `data/evals/valuation_diag/2026-10-17.json` y `2026-10-17-pre-smoothing.json` (antes del suavizado de las cíclicas). Base anterior: `2026-10-11-bloque-a.json`.

| | Bloque A (2026-10-11) | Bloque B (2026-10-17) |
|---|---|---|
| Errores de datos | 0 | 0 |
| Mediana DCF/precio, calidad (15) | 0,55 | 0,49 |
| Mediana EPV/precio, calidad | 0,23 | 0,30 |
| Rentabilidad implícita, mediana de calidad | — | 6,2 % (listón 10,1 %) |
| Comprables hoy (precio ≤ entrada) | 0 de 30 | 5 de 33: MOS, VZ, BBW, PRDO, KBR |
| Comprables con el precio −40 % | solo BBW | 12, de ellas 3 de calidad: AZO, PEP, HD |
| Comprables con el precio +40 % | — | 2: VZ y BBW, que rinden un 16–19 % a precio de hoy |
| Pesimista fija la entrada | — | PFE, CALM |
| Sin entrada | — | INTC (deuda > EV), PATH, PLUG (sin DCF positivo) |

**Lectura:**
- **DCF de calidad:** baja un ~10 % por la transición del fade y el NOPAT normalizado.
- **EPV:** sube un ~30 % al dejar la mediana en dólares.
- **La decisión cambia de naturaleza:** antes no era alcanzable; ahora discrimina. Compra lo que rinde por encima del listón con un pesimista razonable y deja en WATCHLIST la calidad cara (4–8 % implícito).
- **MOS:** pasa a comprable al suavizar las cíclicas (base 11,5 %, pesimista 6,0 %).
- **CALM:** sigue en WATCHLIST, con un pesimista ligado a la gripe aviar.
