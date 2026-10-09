# ADR-034: Normalización cíclica — las cíclicas se valoran en su nivel mid-cycle

**Status**: Accepted (2026-10-09, implemented post-H1, EC-92)
**Date**: 2026-10-09
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `valuation`, `cyclicals`, `mid-cycle`, `damodaran`, `graham`
**Amends**: [ADR-033](ADR-033-fcff-wacc-equity-bridge.md) (base del DCF, EPV, valor terminal y histórico del reverse DCF cuando la empresa es cíclica)

## Context

Primer análisis real tras H1 (Cal-Maine, CALM, 2026-10-03): DCF $205/acción con precio $66,67 (+208 %). La base FCFF (mediana de 3 años) incluía el año de gripe aviar (margen operativo 36 % frente a 7,5 % típico) y el crecimiento de la etapa 1 (16,6 %) salía del CAGR de ventas inflado por el mismo pico. El reverse DCF salía "barato" y la convergencia ("3 de 4 baratos") se usaba como argumento. El Critic lo detectó; la capa de valoración no.

## Decisions

1. **Detector determinista** (`valuation_math.assess_cyclicality`): con ≥ 5 ejercicios de margen operativo (hasta 10), una empresa es cíclica si su mejor margen supera la mediana en **≥ 8 pp** y es **≥ 1,75×** la mediana (con mediana ≤ 0 basta el exceso). La señal son los **picos hacia arriba**: un hundimiento puntual (BBW 2020) o una recuperación (PRDO) no es un ciclo, y la desviación típica no distingue esos casos.
   - Calibrado ANTES de escribir código con márgenes reales de 10 años: no cíclicas AAPL (+3 pp), AZO (+1), BBW (+1), PRDO (+6); cíclicas NUE (+16, 2,8×), MOS (+18, 3,8×), CALM (+29, 4,8×).
2. **Nivel mid-cycle = margen mediano (≤ 10 años) × ventas actuales** (Damodaran, *normalized margin*). Se aplica a la vez a: base FCFF del DCF, EBIT del EPV, NOPAT del valor terminal y fallback NOPAT. Un único nivel para todos los métodos (coherencia de ADR-033).
   - Descartado: mediana del EBIT en dólares de 10 años — ignora el cambio de tamaño (CALM triplicó ventas; EPV $41 frente a $58).
3. **g1 = crecimiento terminal** para cíclicas salvo override del usuario en el checkpoint 2. El CAGR entre dos años depende de dónde caen los extremos del ciclo, no del negocio. El crecimiento estructural (p.ej. capacidad nueva) lo justifica el inversor.
   - Descartado: CAGR de ventas de 10 años (CALM 11,7 %, sigue inflado por precios).
4. **Reverse DCF de cíclicas comparado con el CAGR de ventas de toda la ventana**, no el de 5 años.
5. **Transparencia en cada capa**: `ValuationReport.cyclicality`, línea "Cyclical: yes" en el informe, bloque `## CYCLICALITY (deterministic)` y aviso en el bloque de CAGR histórico del Value Expert, y aviso en el checkpoint 2 antes de pedir g1.
6. La nota "DCF growth capped at 30 %" solo aparece si el valor usado es ese tope (antes aparecía junto al tope por ventas, contradiciéndolo).

## Consequences

- CALM: DCF $205 → $70 (+8 %), EPV $82,5 → $58,7; convergencia de "3 baratos" a "1 barato, 2 razonables". Veredicto HOLD estable.
- CALM es el 5.º arquetipo (MCE-25).
- **Pendientes**: las ventas también suben en el pico de una materia prima (normalizarlas exige precios mid-cycle, no disponibles); la valoración no usa datos TTM (CALM ya pierde dinero en el Q1 FY2027). Ver EDGE_CASES "Conocidos — pendientes".

## References

- Damodaran, *The Little Book of Valuation* / *Investment Valuation*, cap. sobre empresas cíclicas (normalized earnings).
- Graham & Dodd: beneficio medio de 7–10 años.
- Código: `agents/valuation_math.py` (`assess_cyclicality`), `agents/valuation.py` (`_cyclicality`, `_mid_cycle_ebit`, `_build_fcff(cyclical=True)`). Tests: `tests/test_valuation_cyclical.py`.
