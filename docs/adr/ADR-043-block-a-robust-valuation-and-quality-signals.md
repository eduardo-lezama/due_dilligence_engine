# ADR-043: Bloque A — valoración robusta sin sesgo y señales de calidad deterministas

**Status**: Accepted (2026-10-11)
**Date**: 2026-10-11
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `valuation`, `growth`, `risk-free-rate`, `accounting-scores`, `outcomes`
**Supersedes (parcialmente)**: [ADR-022](ADR-022-small-cap-display-and-dcf-growth-methodology.md) en la regla de crecimiento de la etapa 1
**Related**: [ADR-029](ADR-029-three-stage-dcf-and-epv.md), [ADR-033](ADR-033-fcff-wacc-equity-bridge.md), [ADR-034](ADR-034-cyclical-normalization.md), [ADR-038](ADR-038-value-expert-eval-harness.md), [ADR-042](ADR-042-data-normalization-at-edgar-boundary.md), `docs/research/2026-10-11-valuacion-por-perfiles.md`

## Context

El diagnóstico del 2026-10-11 mostró que las empresas de calidad "valían" ~0,41× su precio. El plan de valoración por perfiles (F1–F6) se dejó en espera (MCE-39) por riesgo de sobreingeniería: muchos parámetros sin una verdad contra la que medirlos.

En su lugar se ejecutó un bloque de mejoras que cumplen tres condiciones:
- sirven para cualquier empresa;
- tienen respaldo publicado;
- añaden poca maquinaria.

Antes de tocar nada se midió dónde estaba el sesgo:

| Hallazgo | Detalle |
|---|---|
| La base del DCF ya estaba normalizada | Mediana del margen FCFF de **3** años. Con dos años anómalos (KO 2024–2025) la mediana se hundía: 12 % frente a ~22 %. |
| El crecimiento era la causa principal | g1 = CAGR del FCF: KO −9,4 % con ventas +7,7 %. |
| Parte del "sesgo" era mercado caro de verdad | EV/FCFF de 51× (AAPL), 53× (COST) y 57× (MSFT). No se corrige. |

## Decisions

1. **Base normalizada de 5 años (A1).**
   - **Qué:** mediana del margen FCFF de hasta 5 años × ventas actuales. Las cíclicas siguen con 10 años (ADR-034).
   - **Por qué:**
     - es like-with-like con el EPV, que ya usaba 5 años de EBIT;
     - sigue la regla de Graham (beneficio de varios años);
     - el efecto es simétrico: KO sube, mientras HD, BBW y KBR bajan.
2. **Crecimiento de la etapa 1 = tendencia de ventas por mínimos cuadrados (A1).**
   - **Qué:** ajuste log-lineal sobre hasta 10 años (≥ 3 puntos), acotado a [−15 %, 15 %].
   - **Por qué ventas:** la base es "margen × ventas", así que con margen constante el FCFF crece como las ventas.
   - **Por qué tendencia y no CAGR:** el CAGR depende del año de inicio. Con 5 años arrancaba en el mínimo de 2020 (XOM 13,2 %, PFE 8,3 %); la tendencia da XOM 5,9 % y PFE 3,2 %, y en negocios estables coincide con el CAGR (MSFT 14,9 %). Es el método "least-squares growth rate" que usa el Banco Mundial.
   - **Por qué el techo del 15 %:** el crecimiento de ventas persiste poco (Mauboussin: correlación 0,16 a 3 años).
   - **Override del usuario:** se mantiene hasta el 30 %, con su tesis en el checkpoint 2.
   - **Fallbacks, en orden:** CAGR de ventas a 5 años → CAGR del FCF → 7 % genérico.
3. **Tipo sin riesgo vivo (A5).**
   - **Qué:** bono a 10 años de FRED (DGS10, CSV sin clave), leído en el nodo de mercado (`MarketReport.risk_free_rate`). Se usa en Ke y en el coste de la deuda.
   - **Sin dato:** se usa el 4,2 % estático y el desglose del WACC lo dice.
   - **Efecto el 2026-10-08:** 5,22 % frente a 4,2 %; el WACC sube ~1 pp.
   - **Lo que no cambia:** la prima de riesgo (5 % histórica). Su elección queda para MCE-39.
4. **Expectativas del precio visibles (A4).**
   - **Qué:** bloque "What the price implies" al principio de la valoración del informe: crecimiento que descuenta el precio frente al CAGR de ventas histórico.
   - **Lectura:** con la misma banda de ±3 pp que el método "Reverse DCF".
   - **Datos:** solo reformula números ya calculados (expectations investing, Mauboussin y Rappaport).
5. **Scores contables deterministas (A2).**
   - **Qué:** Piotroski F, Altman Z'', Beneish M y accruals de Sloan, con las fórmulas y umbrales publicados (`tools/accounting_scores.py`). Se muestran en el informe.
   - **Si falta un insumo:** "n/a — missing: …"; nunca un valor neutro.
   - **Patrimonio negativo:** Z'' sale "n/m".
   - **Extracción nueva por año:** activo corriente, beneficios retenidos, PP&E neto, cuentas a cobrar y SG&A (derivado como S&M + G&A si hace falta).
   - **No se deriva el beneficio bruto** (EC-104).
6. **DCF en tres escenarios (A3).**
   - **Margen FCFF:** P25 / mediana / P75 de la ventana de normalización.
   - **Crecimiento:** mínimo / base / máximo de {g1, CAGR de ventas a 3, 5 y 10 años}, con los mismos límites.
   - **Lo que no varía:** WACC y terminal, que ya tienen la tabla de sensibilidad.
   - **Camino de cálculo:** el mismo que el titular. Un test exige que el escenario base reproduzca el valor por acción.
   - **Veredicto:** sigue anclado al base (ADR-041 sin cambios).
7. **Higiene (A6).**
   - consola a prueba de logs no ASCII;
   - revenue fusionado año a año;
   - línea total de ingresos cuando el concepto elegido difiere > 10 % (EC-105);
   - auditoría de tests con fechas (MCE-38: ninguno caduca).
8. **Seguimiento de aciertos (A7, MCE-36).**
   - **Qué:** `scripts/outcomes_report.py` calcula la rentabilidad frente a SPY por metodología y veredicto.
   - **Precios:** ajustados por splits en ambos extremos; se reconstruyen para filas antiguas.
   - **Duplicados:** un único análisis por (ticker, fecha).
   - **Días:** aparecen en cada grupo.
9. **Puerta de evals.** El dataset del harness guarda la valoración congelada con el código anterior, así que pasar la puerta ahora no ejercitaría ningún cambio.
   - **Rama nueva del Expert** (descripción de la fuente del crecimiento): cubierta con tests unitarios.
   - **Re-congelar el dataset y regenerar las baselines:** pasa a B4 del bloque B (2026-10-17). Se hará UNA vez, después de B1–B3, que vuelven a cambiar lo que recibe el Expert; hacerlo ahora sería pagar dos veces. La deuda queda registrada en `tests/evals/baselines/pending_gate.json`.

## Alternatives considered

- **Perfiles de empresa completos** (F1–F6: betas por sector, umbrales de moat, duración de la ventaja). En espera con trigger (MCE-39); demasiados parámetros sin verdad.
- **CAGR de ventas a 5 años como crecimiento** (primera versión de A1). Rechazado tras medirlo: hereda el rebote post-COVID (XOM 0,30 → 1,14 de DCF/precio).
- **Matriz de transición de Mauboussin como estimador.** No se pudo verificar la tabla completa en la fuente primaria (PDF con gráficos). No se meten en el código números sin verificar.
- **Derivar el beneficio bruto** (ingresos − coste de ventas). Rechazado tras verificarlo con datos reales (EC-104).
- **Preferir siempre la línea `Revenues`.** Rechazado tras medirlo en 33 empresas (EC-105).
- **Pasar la puerta de evals con el dataset actual.** Pagar por no medir nada; se propone re-congelar.

## Consequences

- **Más creíble y más honesta:** la valoración depende menos de años sueltos y de extremos de ventana, y muestra un rango además de una cifra.
- **Calidad contable:** las trampas de valor y la contabilidad agresiva tienen señal determinista en el informe.
- **WACC realista:** con el tipo vivo, el WACC sube ~1 pp respecto a mayo. Es correcto, y empuja las valoraciones a la baja.
- **El dataset de evals queda desfasado** respecto al código hasta que se re-congele.
- **Revisión de código** (`/code-review high`): 10 hallazgos. Se corrigieron 8:
  - el terminal de los escenarios no escalaba con el margen;
  - el optimista quedaba por debajo del base con override;
  - regresión del relleno de huecos (EC-88);
  - etiquetas no FY;
  - texto roto `fcf_cagr_5y_capped` para el Expert;
  - afirmación falsa de "margen de 5 años";
  - FRED descargando la serie completa;
  - etiquetas del CLI.

  Quedan dos:
  - **#7, la puerta:** pasa a B4 del bloque B (ver decisión 9).
  - **#9, refactor menor:** no se hace.

## Resultados (diagnóstico de 33 empresas, sin LLM, 2026-10-11)

| Etapa | Mediana DCF/precio (calidad, 15) | Mediana DCF/precio (todas) | > 2 | < 0,5 | Errores |
|---|---|---|---|---|---|
| Antes de F0 | 0,41 | 0,49 | 5 | 14 | 2 |
| Tras F0 (ADR-042) | 0,41 | 0,47 | 3 | 16 | 0 |
| A1 con CAGR a 5 años (descartado) | 0,65 | 0,71 | 3 | 6 | 0 |
| **Bloque A final** (Rf 5,22 %) | **0,55** | **0,62** | **1** | **10** | **0** |

**Lectura:**
- **Banda:** la mediana de calidad entra en la banda [0,5; 0,9] que la investigación consideraba razonable sin forzarla.
- **El resto hasta 1 es mercado caro:** EV/FCFF > 50× en AAPL, COST y MSFT, con una prima de riesgo implícita baja.
- **Extremos que se explican:**
  - VZ (2,03): FCF alto frente a precio.
  - KBR (1,93).
  - KO (0,34) y MCD (0,46): la tendencia de 10 años recoge el refranchising de 2016–2018, que hundió sus ventas. El optimista los sitúa en 0,45 y 0,74; si los informes reales lo confirman como fallo, es candidato al trigger de MCE-39.
- **Data:** `data/evals/valuation_diag/2026-10-11-{pre-f0,post-f0,a1,bloque-a}.json`.
