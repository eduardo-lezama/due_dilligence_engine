# Diagnóstico de la valoración y dirección de arquitectura (2026-10-11)

**Contexto.** H2-2 (ADR-041) calcula el margen de seguridad y el precio de entrada a partir del DCF y el EPV. Antes de construir más encima se paró a comprobar si esos valores intrínsecos son creíbles. La pregunta vino de una revisión crítica pedida por Eduardo: ¿estamos mejorando el due diligence o puliendo etiquetas?

**Método.** `scripts/valuation_diagnostic.py` ejecuta solo los nodos deterministas (supervisor → financials → precio de Polygon → valuation, con los defaults del checkpoint 2) sobre 33 empresas estadounidenses:
- 15 de calidad o defensivas;
- 6 cíclicas;
- 5 maduras o en dificultades;
- las 7 del dataset de evals.

Se excluyen bancos y aseguradoras. Coste de LLM: 0 $. Datos: `data/evals/valuation_diag/2026-10-11.json` (local).

## Resultado agregado

| Grupo | n | Mediana DCF/precio | Mediana EPV/precio | DCF/precio > 1 |
|---|---|---|---|---|
| Calidad y defensivas | 14 | **0,41** | **0,35** | 2 (V y MA, por un bug) |
| Todas con dato | 28 | 0,49 | 0,41 | 9 |

Según el modelo, Microsoft, Apple, Costco, Coca-Cola, P&G o J&J valdrían el 30–40 % de su precio. Que el mercado sobrevalore a todas un 60 % de forma persistente no es creíble: **la valoración tiene un sesgo sistemático a la baja**. Además, DCF y EPV de la misma empresa discrepan hasta 3–4× (KBR 3,43; NKE 0,25).

## Causas, por impacto

1. **El crecimiento sale de la tasa de crecimiento anual (CAGR) de 5 años del flujo de caja libre (FCF). Es la causa principal.** El FCF es ruidoso, y una caída puntual se extrapola a una década:
   - **KO:** el FCF pasa de 9,75 B$ (2023) a 4,74 B$ (2024) por un pago extraordinario. Resultado: g = −9 % y DCF/precio = 0,11.
   - **Otras con g negativo:** ORLY −8 %, AZO −5 %, PFE −6 %, XOM −10 %, NKE −15 %, PG −1 %.
   - **MSFT y GOOGL:** penalizadas por un ciclo de inversión (capex en IA) que hunde el FCF.
   - El crecimiento solo se acota por arriba (con el CAGR de ingresos), nunca por abajo.
2. **Bug de acciones con varias clases.** V cuenta 0,469 B acciones (son ~1,9 B) y MA 0,123 B (diluidas 0,906). El valor por acción sale inflado entre 3 y 7 veces, y por eso V y MA aparecen "baratas" (2,7× y 3,7×). Probablemente afecte también a GOOGL (clases A, B y C). Es la misma familia de problema que EC-93 (insiders multiclase).
3. **Bug de periodo fiscal en minoristas.** HD y TGT fallan con `DataUnavailableError: latest 10-K period …`. Son cierres fiscales a finales de enero o principios de febrero; la etiqueta FY del 10-K no coincide con la última columna de los estados.
4. **Beta = 1 para todas las empresas.** El WACC queda en ~9 % casi siempre, y los negocios defensivos (KO, PG, JNJ) se descuentan como si tuvieran el riesgo del mercado. El efecto es menor que el de la causa 1, pero en la misma dirección.
5. **El moat lo decide el LLM y sale demasiado severo.** En el dataset, 12 de 13 empresas salen "sin moat". Sin moat no se puede ser "compounder de calidad" (ADR-041), lo que impone un margen del 50 % y un ancla mín(DCF, EPV). Consecuencia: **se pierden oportunidades de compra** en negocios buenos.

## Causa raíz y dirección

**Un mismo método de valoración y un mismo prompt de 24 reglas para todo tipo de empresa.** Un analista no valora igual una empresa madura y estable, un compounder, una cíclica o una empresa en reestructuración. Es el enfoque de Damodaran por ciclo de vida y por sector, y los sistemas recientes de análisis con LLM (FinRobot, "industry-appropriate valuation metrics") van en la misma línea.

**Dirección acordada con Eduardo:** arquitectura por **perfil de empresa**.

- **Clasificador mayormente determinista** al principio del grafo: sector (SIC), rentabilidad, crecimiento, ciclicidad y ROIC histórico. El LLM solo desempata lo cualitativo.
- **Valoración por perfil, en código.** Ejemplos: crecimiento a partir de ROIC × reinversión para los compounders; base de caja normalizada para las maduras; mitad de ciclo para las cíclicas; EPV o liquidación para las que están en declive.
- **Paquete de reglas del Expert por perfil:** un núcleo común más las reglas propias del perfil (absorbe H2-4).
- **Moat con evidencia cuantitativa** (ROIC sostenido por encima del coste de capital durante 7–10 años). El LLM aporta la explicación, no un veto.
- **"Método no aplicable"** explícito para lo que no se valora bien (bancos, REITs, biotech sin ingresos), en lugar de un análisis de aspecto normal.

**Se rechaza** un supervisor LLM que elija herramientas o caminos de forma dinámica: es poco fiable, difícil de testear y choca con los invariantes. Es el mismo DAG de LangGraph con un nodo más y la valoración ramificada.

**Universo objetivo** (Eduardo): empresas estadounidenses, sin nicho, infravaloradas y analizables con fiabilidad. Herramienta "todoterreno".

**Cómo se validará:**
- Volver a ejecutar este diagnóstico (gratis): las medianas de calidad deben salir razonables y las discrepancias DCF/EPV explicadas, sin forzar que el valor se parezca al precio.
- La puerta de evals para el Expert.

## Relación con la auditoría de 2026-10-02

Este diagnóstico **reordena** la hoja de ruta de `docs/audits/2026-10-02-architecture-assessment.md` (§6–§7) sin contradecirla. Varios ítems que allí estaban en H3 (beta por industria de Damodaran, normalización cíclica y owner earnings, g = ROIC × reinversión, EPV frente a valor de reproducción para el moat) resultan ser **cimientos** y pasan delante. Lo único nuevo es el clasificador de perfil. Se mantienen las restricciones de "Lo que NO hacer": nada de orquestador agéntico ni de reglas sin check. La trazabilidad completa, ítem a ítem, está en el plan vigente.

## Tabla completa

| Ticker | Precio | DCF/precio | EPV/precio | DCF/EPV | WACC | g usado | Fuente de g | % valor terminal | Error |
|---|---|---|---|---|---|---|---|---|---|
| AAPL | 336.64 | 0.36 | 0.23 | 1.59 | 9.1 | 6.1% | fcf_cagr_5y | 49% |  |
| MSFT | 535.07 | 0.34 | 0.24 | 1.42 | 9.1 | 3.6% | fcf_cagr_5y | 59% |  |
| GOOGL | 351.66 | 0.41 | 0.19 | 2.19 | 9.1 | 11.3% | fcf_cagr_5y | 65% |  |
| V | 385.45 | 2.69 | 1.02 | 2.64 | 9.0 | 12.9% | fcf_cagr_5y_rev_capped | 48% |  |
| MA | 589.14 | 3.75 | 1.61 | 2.33 | 9.0 | 6.8% | fcf_cagr_5y_rev_capped | 44% |  |
| COST | 946.92 | 0.41 | 0.22 | 1.88 | 9.1 | 9.1% | fcf_cagr_5y_rev_capped | 53% |  |
| ORLY | 86.45 | 0.20 | 0.33 | 0.62 | 8.8 | -7.6% | fcf_cagr_5y | 43% |  |
| AZO | 2939.85 | 0.34 | 0.51 | 0.66 | 8.3 | -4.6% | fcf_cagr_5y | 50% |  |
| ADP | 270.88 | 0.80 | 0.38 | 2.10 | 9.0 | 7.9% | fcf_cagr_5y_rev_capped | 45% |  |
| MCD | 235.23 | 0.81 | 0.43 | 1.90 | 8.1 | 7.0% | fcf_cagr_5y_rev_capped | 55% |  |
| KO | 88.05 | 0.11 | 0.22 | 0.49 | 8.6 | -9.4% | fcf_cagr_5y | 54% |  |
| PEP | 125.97 | 0.63 | 0.45 | 1.41 | 8.0 | 3.8% | fcf_cagr_5y | 52% |  |
| PG | 151.23 | 0.42 | 0.38 | 1.11 | 8.7 | -0.6% | fcf_cagr_5y | 45% |  |
| JNJ | 261.44 | 0.29 | 0.26 | 1.12 | 8.9 | -0.5% | fcf_cagr_5y | 41% |  |
| HD | — | — | — | — | — | — | — | — | DataUnavailableError: HD: latest 10-K period  |
| CAT | 800.47 | 0.49 | 0.20 | 2.39 | 8.8 | 10.1% | fcf_cagr_5y_rev_capped | 46% |  |
| DE | 620.93 | 0.50 | 0.47 | 1.08 | 8.8 | -1.8% | fcf_cagr_5y | 39% |  |
| NUE | 250.33 | 0.45 | 0.39 | 1.16 | 8.6 | 2.5% | cyclical_terminal | 51% |  |
| XOM | 168.94 | 0.30 | 0.61 | 0.49 | 8.9 | -10.0% | fcf_cagr_4y | 38% |  |
| CVX | 211.98 | 1.40 | — | — | 8.7 | 14.3% | fcf_cagr_5y_rev_capped | 62% |  |
| MOS | 18.74 | 1.48 | 1.00 | 1.48 | 6.9 | 2.5% | cyclical_terminal | 51% |  |
| NKE | 34.71 | 0.44 | 1.78 | 0.25 | 8.6 | -15.0% | fcf_cagr_5y | 40% |  |
| TGT | — | — | — | — | — | — | — | — | DataUnavailableError: TGT: latest 10-K period |
| INTC | 104.70 | — | -0.04 | — | 9.7 | -7.5% | revenue_cagr_fallback | — |  |
| PFE | 28.28 | 0.19 | 0.17 | 1.13 | 7.8 | -5.7% | fcf_cagr_5y | 31% |  |
| VZ | 41.65 | 2.41 | 1.16 | 2.08 | 6.7 | 1.5% | fcf_cagr_2y_rev_capped | 39% |  |
| BBW | 25.53 | 3.99 | 1.79 | 2.23 | 9.2 | 15.7% | fcf_cagr_5y_rev_capped | 60% |  |
| PRDO | 33.71 | 1.37 | 0.89 | 1.53 | 9.2 | 4.2% | fcf_cagr_5y_rev_capped | 45% |  |
| KBR | 35.34 | 2.11 | 0.62 | 3.43 | 7.3 | 6.2% | fcf_cagr_5y_rev_capped | 61% |  |
| QLYS | 202.42 | 0.65 | 0.24 | 2.74 | 9.2 | 13.0% | fcf_cagr_5y_rev_capped | 48% |  |
| CALM | 64.35 | 1.09 | 0.91 | 1.19 | 9.2 | 2.5% | cyclical_terminal | 42% |  |
| PATH | 13.48 | — | — | — | 9.2 | 2.5% | cyclical_terminal | — |  |
| PLUG | 1.68 | — | — | — | 9.3 | 2.5% | cyclical_terminal | — |  |
