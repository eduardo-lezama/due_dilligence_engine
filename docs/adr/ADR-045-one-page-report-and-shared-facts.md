# ADR-045: Informe de una página, hechos deterministas compartidos y comparables sugeridos

**Status**: Accepted (2026-10-17, bloque B)
**Date**: 2026-10-17
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `report`, `value-expert`, `peers`, `consolidation`
**Related**: [ADR-009](ADR-009-verdict-plus-analysis.md), [ADR-015](ADR-015-peer-comparison-user-supplied.md), [ADR-044](ADR-044-decision-by-expected-return-vs-hurdle.md), `docs/audits/2026-10-17-big-picture-review.md`

## Context

Hallazgos de la revisión del 2026-10-17:
- **El informe no ayudaba a decidir** (4/10 según un red team con ojos de gestor):
  - unas 4.000 palabras, ~40 % de noticias y apéndices;
  - no decía qué hacer ni a qué precio;
  - no respondía "¿barata frente a qué?": "Peer comparison skipped by user" en todos los informes de octubre.
- **El formato estaba triplicado.** La misma cifra se redactaba en el contexto del Expert (720 líneas), en el markdown (444) y en la consola, y un arreglo en un sitio no llegaba a los otros (EC-14 → EC-17).
- **Lo nuevo no llegaba a la decisión.** Los scores contables y los escenarios del bloque A solo aparecían en el markdown: el Expert no los veía.

## Decisions

1. **`agents/report_facts.py`: fuente única de los hechos que deciden.**
   - **Secciones:**
     - entradas de la decisión (rentabilidad frente al listón y entrada);
     - qué implica el precio;
     - rango de valor;
     - calidad (ROIC y tendencia, ROIC incremental, conversión FCF, acciones, scores contables);
     - comparables;
     - cambios desde el análisis anterior;
     - foto de mercado.
   - **Formatos:** cada sección se pinta como tabla markdown (informe), como líneas planas (Expert) o como tabla rich (consola).
   - **Hechos compartidos:** la tendencia del ROIC (EC-109) y la cobertura del 10-K (EC-106) viven aquí.
2. **Informe = primera página + apéndice.**
   - **Primera página, en este orden:** decisión, tesis, fallos, rentabilidad frente al listón, qué implica el precio, rango, comparables, calidad, riesgos (§7 + governance), qué haría cambiar la tesis (§9 + pre-mortem del Critic) y cambios desde el último análisis.
   - **Apéndice:** el detalle de la valoración (puente, sensibilidad, convergencia), los datos operativos, §1–§6 y §8 del Expert, el Critic, los comparables, el 8-K, las noticias y las citas.
   - **Duplicados:** ninguna sección del Expert se repite entre la primera página y el apéndice.
3. **Cambios desde el último análisis:** `previous_analysis` lee el análisis completo anterior del ticker en SQLite y muestra precio, entrada, rentabilidad y veredicto. Sustituye el caso de uso de MCE-12 (RAG de memoria) sin vector store.
4. **Comparables sugeridos (B3).**
   - **Origen:** `FinnhubAdapter.fetch_company_peers` (misma industria), calculado en el nodo de mercado para no repetir la llamada al reanudar el checkpoint.
   - **Checkpoint 1:** Enter acepta, "none" salta, o se escribe la lista propia.
   - **Cifras:** siguen saliendo de EDGAR y Polygon (`peers_node`).
   - **Informe:** dice si la lista es automática o del inversor (`peers_source`).
5. **El Expert ve las mismas secciones.** Los scores y los escenarios llegan por fin a la decisión (regla 25 del prompt).

6. **Filtro por industria de los comparables sugeridos** (bloque C, 2026-10-17).
   - **Qué se excluye:** un competidor sugerido automáticamente cuyo SIC de 2 dígitos difiere del de la empresa queda fuera de las medianas y del ranking, siempre que queden ≥ 2 de la misma industria.
   - **Si quedan menos:** se conservan todos y la comparación se marca como de otra industria.
   - **Competidores que escribe el inversor:** nunca se excluyen, solo se marcan.
   - **Estado:** `peers_excluded` y `peers_cross_industry`.
   - **Limitación vista en ACMR:** el SIC de 2 dígitos es grueso. 3559 (maquinaria) frente a 3674 (semiconductores) deja todos los sugeridos "de otra industria", aunque AMKR o FORM son vecinos razonables.

## Alternatives considered

- **Reescribir de golpe los tres renderizadores** sobre un modelo de secciones completo. Riesgo alto sin cambio que lo pida. Se movió a `report_facts` lo que decide; el detalle estable queda para MCE-42.
- **Peers por código SIC** desde EDGAR. Exige descargar las submissions de miles de empresas. Finnhub ya está en el stack y solo propone la lista.
- **Múltiplos frente a la propia historia de 10 años.** Necesitan precios históricos que el plan gratuito de Polygon no da (MCE-40).
- **Vector store para "qué ha cambiado".** Una consulta a SQLite basta (YAGNI).

## Consequences

- **El gestor lee la decisión primero**, con los mismos números que el Expert y la consola.
- **Arreglos de un solo sitio:** corregir un hecho compartido es editar un sitio.
- **El contexto del Expert crece algo:** secciones nuevas, y ya no está el bloque de peers anterior. Lo medirá la puerta en B4.
- **Comparables por defecto:** un análisis tiene comparables salvo que el inversor los salte, y añade llamadas a EDGAR y Polygon por cada competidor (límite de 5).
