# ADR-035: Los datos de tablas de documentos primarios se leen por estructura, no los produce el LLM

**Status**: Accepted (2026-10-09, implemented post-H1, EC-93)
**Date**: 2026-10-09
**Deciders**: Eduardo (AI/Agentic Engineering Lead)
**Tags**: `data`, `def14a`, `insider-ownership`, `determinism`, `testing`
**Implements**: [ADR-005](ADR-005-hallucination-control-by-architecture.md) (invariante 1) para la participación de directivos
**Related**: [ADR-032](ADR-032-structured-outputs-fail-loud.md) (los números del LLM pasan solo con cita verificada)

## Context

Management llevaba 0 citas verificadas en todos los análisis posteriores a H1. El LLM producía `insider_ownership_pct` a partir del texto del proxy, y ese texto a menudo no contenía la tabla:
- el recorte por títulos caía después de la tabla cuando el título no coincidía (CALM, PRDO);
- `document.text()` trocea tablas estrechas (AZO);
- el extractor nativo de edgartools 5.59.1 devuelve vacío (`Filing.html()` → None en proxies inline XBRL) y convierte "*" en 0,5 %.
Sin tabla, el LLM sacaba el número de la web o lo inventaba (contextos congelados: AAPL "0.000063 %", MOS "5.57 %"; la tabla dice "*" — menos del 1 %).

## Decisions

1. **Una cifra que está en una tabla del documento primario se lee de la tabla en código** (`tools/proxy_ownership.py`), recorriendo `<table>/<tr>/<td>` del HTML (`EdgarManagementAdapter.fetch_def14a_html`, vía `sgml().html()`). El LLM ya no tiene ese campo en su esquema de salida (mismo patrón que el conteo de Form 4).
2. **Regla de una sola clase**: la última celda es el porcentaje ("3.6"+"%", "9.63%", "*", "*%", "Less than 1%"); las acciones son el último entero anterior (en tablas con columnas de RSU/opciones es el total). Sin porcentaje no es fila de participación (así caen tablas de premios y notas al pie que dicen "as a group").
3. **Nunca una cifra falsa**:
   - "*" / "less than 1%" → `below_1pct=True` y % = None (no 0,5).
   - Tabla con varias clases o varias columnas de % → **fila literal**, sin % único. Detección: cabecera con ≥ 2 clases / "voting power" / "voting common", o cualquier fila con ≥ 2 celdas de %, o una celda que nombra la clase.
   - Sin tabla legible → None y log; nunca se adivina.
4. **Elección de la fila del grupo**: la de la tabla con más filas válidas (la de participaciones), no la primera coincidencia; una tabla de una sola fila vale si es la del grupo.
5. **Fecha del proxy** junto al dato; > 15 meses → aviso de dato desfasado.
6. **El LLM recibe la tabla limpia** ("label | N shares | X%") como fuente citable; la cita de la fila del grupo la construye el código (verificada por construcción).
7. **Verificación adversarial con datos reales antes de aceptar un parser**: revisores independientes ejecutan el parser sobre emisores que el autor no eligió. Cada fallo encontrado se convierte en fixture (`tests/fixtures/<ticker>_def14a_ownership.html`).
8. **Guard de red en tests ampliado** a `edgar.Company`, `finnhub.Client` y `polygon.RESTClient`, con mocks por defecto de 8-K, Finnhub y HTML del proxy. Los fixtures autouse usan `unittest.mock.patch`, no `mocker` (orden de teardown).

## Consequences

- CALM: management 0/1 → 3/3 citas verificadas; 3,6 % (1.697.343 acciones). Doble clase visible por primera vez (PATH: 85,2 % del voto en manos de directivos).
- Revisión adversarial en vivo (33 emisores): 5 cifras equivocadas en la primera versión (NKE, NWSA, HEI, UHAL, SPG), corregidas con fixtures; 3 casos sin encontrar recuperados (F, BF-B, ACN).
- Suite de tests 38 s → 6 s: había llamadas reales a EDGAR/Finnhub/Polygon ocultas.
- **Pendientes**: CRM y DVN devuelven None (formatos atípicos); tablas como imagen → None.

## Alternatives considered

- `ProxyStatement.beneficial_ownership` de edgartools: vacío para estos proxies y convierte "*" en 0,5 %. Rechazado.
- Añadir más títulos a la búsqueda en texto: parche frágil (otro título nuevo rompe igual) y no arregla tablas troceadas. Rechazado.
- Dejar el % al LLM con cita obligatoria: el LLM no puede citar lo que no recibe y parafrasea tablas. Rechazado.

## References

- Código: `tools/proxy_ownership.py`, `tools/edgar.py` (`fetch_def14a_html`), `agents/management.py` (`_build_report`, SOURCE 1b). Tests: `tests/test_proxy_ownership.py`, `tests/test_network_guard.py`.
- Checklist §3.1 (insider ownership [quant], fuente DEF 14A).
