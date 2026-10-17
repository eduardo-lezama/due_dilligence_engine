# ADR-042: Normalización de datos de EDGAR en la frontera (F0: periodo fiscal, acciones, caja, beneficio neto)

**Status**: Accepted (2026-10-11, F0 del plan "valoración por perfiles")
**Date**: 2026-10-11
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `edgar`, `xbrl`, `data-integrity`, `valuation`
**Related**: [ADR-005](ADR-005-hallucination-control-by-architecture.md) (cifras deterministas), [ADR-035](ADR-035-document-tables-deterministic.md) (EC-93: leer el documento original), `docs/audits/2026-10-11-valuation-diagnostic.md`

## Context

El diagnóstico de valoración del 2026-10-11 (33 empresas, sin LLM) mostró dos bugs de datos:
- **HD y TGT** no se podían analizar: `DataUnavailableError` por desalineación de periodo.
- **V y MA** salían "baratas" (DCF/precio 2,7 y 3,7) por un recuento de acciones erróneo.

Al verificarlos con datos reales aparecieron otros dos: la caja de TGT (0,25 B$ frente a 5,49 B$) y el beneficio neto vacío de MA y CAT. Los cuatro tienen la misma forma:
- la fuente agregada (companyfacts y la vista curada de edgartools) pierde o deforma información para una clase de empresas;
- el sistema la aceptaba como si fuera completa.

Eduardo pidió que cada arreglo sirviera para analizar cualquier empresa, sin sesgos ni casos particulares por ticker.

## Decisions

1. **Los datos se normalizan en la frontera** (`tools/xbrl_extract.py`, funciones puras con tests de DataFrames sintéticos). Los agentes reciben siempre el mismo contrato, con `source` por valor. No se reescriben reglas internas de edgartools.
2. **Último ejercicio perdido por edgartools (EC-100).**
   - **Qué hace:** `restore_latest_fy_column` reconstruye la columna desde raw facts, emparejando por period_end.
   - **Cuándo actúa:** solo en el caso exacto y comprobable. Tiene que faltar exactamente un año, raw facts tiene que tener el revenue de ese año, y el revenue del año anterior tiene que coincidir ±0,5 %.
   - **En cualquier otro caso:** se mantiene el fallo explícito (EC-87).
3. **Recuento de acciones con regla de frescura (EC-101).** Nunca se usa un dato con fecha anterior al cierre del último 10-K.
   - **Primera opción:** la portada de companyfacts, si es fresca.
   - **Si no:** el XBRL del propio 10-K.
     - Una clase → su portada.
     - Varias clases → la diluida "as-converted" que publica la empresa (≥ 95 % de la suma de portada).
     - Sin ella → suma de clases, con aviso.
   - **Si nada es fresco:** None, con aviso; la valoración usa las diluidas medias.
4. **Sinónimos XBRL ordenados del más amplio al más estrecho (EC-102).**
   - **Caja:** la línea combinada (caja + inversiones a corto) va antes que `Cash`.
   - **Coherencia con las inversiones a corto:** si la caja sale de la línea combinada, las inversiones a corto se marcan como "incluidas" para no restarlas dos veces.
5. **Conceptos alternativos resueltos año a año (EC-103).** Beneficio neto = `NetIncomeLoss` → `ProfitLoss − minoritarios` → `ProfitLoss` etiquetado, con la fórmula en `source`. La rejilla de años es la del revenue unida a las columnas del estado de resultados.
6. **Verificación obligatoria con barrido real.** Antes de dar por bueno un cambio de extracción se comparan, sobre el universo del diagnóstico:
   - caja, inversiones a corto y beneficio neto, antes y después;
   - los arquetipos, que deben quedar idénticos a la línea base.

   Este barrido detectó dos fallos en las primeras versiones:
   - leer siempre del 10-K movía a las empresas de una sola clase;
   - la rejilla del beneficio neto recortaba años a PFE.

## Ampliación (bloques B y C, 2026-10-17)

La misma regla, normalizar en la frontera y dejar un flag visible, se aplica a dos datos más:
- **Acciones tras un split (EC-112):** `adjust_for_splits` reexpresa los años anteriores de las series ponderadas.
- **EBIT sin etiquetar (EC-107):** `resolve_operating_income` trabaja año a año.
  - Si el EBIT está reportado, se usa.
  - Si no, se deriva desde el beneficio antes de impuestos, verificado frente al reportado en 12 empresas.
  - Lo que siga vacío se rellena con la cadena histórica.

## Alternatives considered

- **Copiar la nueva regla de etiquetas FY de edgartools 5.59.** Rechazada: depende del tag `fy` del filing, que es justo lo que colisiona. Además, la regla ya cambió una vez entre versiones y volvería a divergir.
- **Construir los estados enteros desde raw facts, sin la vista curada.** Más robusta a largo plazo, pero es un cambio grande y fuera del alcance de F0. Queda como opción si aparecen más pérdidas de la vista curada.
- **Para las multiclase, sumar las clases de la portada.** Rechazada como regla principal: ignora los ratios de conversión (en V da 1.822 M acciones frente a 1.966 M as-converted, un 8 % menos). Se mantiene solo como último recurso, con aviso.
- **Para la caja combinada, restar el STI de las notas para obtener "solo caja".** Rechazada: el STI de las notas puede no corresponder a la misma partida (TGT: 4,61 B$ dentro de 5,49 B$, sin desglose completo en el balance). Marcar el STI como incluido es neutro para EV e IC y no inventa un desglose.
- **Abrir un issue en edgartools.** Pendiente de decisión de Eduardo (es una acción pública). No sustituye al arreglo local.

## Consequences

- **HD y TGT** se analizan, con un aviso de procedencia del último año.
- **V y MA** dejan de salir baratas por error de datos (DCF/precio 2,69 → 0,64 y 3,75 → 0,51); quedan sujetas al sesgo general que trata F3. **NKE**, que nadie había señalado, también tenía un recuento caducado (855 M de 2015, antes del split; reales 1.481 M): 0,44 → 0,25.
- **Diagnóstico de 33 empresas:** 0 errores de datos (antes 2: HD, TGT). Calidad y defensivas (15): mediana DCF/precio 0,41 (sin cambio) y EPV/precio 0,26 (antes 0,35, inflada por V y MA); ninguna con DCF/precio > 1 (antes 2, ambas por el bug). El sesgo a la baja era real y no venía de estos bugs: queda para F3.
- **MA y CAT** tienen beneficio neto (ROE, payout); **TGT**, caja correcta.
- **Coste:** una descarga del XBRL del 10-K solo cuando companyfacts no tiene portada fresca (en la práctica, las multiclase).
- **Limitación conocida:** TGT no tiene beneficio neto en raw facts antes de FY2021.
- **Pendientes no tratados en F0:**
  - PG y CVX toman la caja del concepto con efectivo restringido (fallback ya marcado en `source`).
  - Las tasas de crecimiento y la beta siguen siendo las mismas para todos; corresponde a F3.
