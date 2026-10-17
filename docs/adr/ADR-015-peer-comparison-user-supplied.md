# ADR-015: Peer comparison user-supplied en Checkpoint 1

**Status**: Accepted — ampliado por [ADR-045](ADR-045-one-page-report-and-shared-facts.md) (2026-10-17): el checkpoint 1 propone por defecto los competidores que sugiere Finnhub; el inversor los acepta, los cambia o los salta. Las cifras siguen saliendo de EDGAR y Polygon.
**Date**: 2026-04-21

## Context

El campo `CompetitiveReport.peer_roic_comparison` existe desde Phase 1b pero está permanentemente vacío. El plan inicial proponía descubrir peers automáticamente por **SIC code** (código de industria de 4 dígitos del SEC). La revisión estratégica de Phase 2 identificó dos problemas serios:

1. **Los SIC codes son ruidosos.** Un mismo 4 dígitos puede contener negocios no comparables. Ejemplo: SIC 3674 ("Semiconductors") agrupa a NVIDIA con fabricantes de componentes commodity. La comparación de ROICs no tiene sentido económico.
2. **Eduardo conoce sus peers mejor que cualquier heurística.** Gestiona una cartera curada; ya sabe contra quién compite cada posición.

Forzar auto-discovery sería añadir una capa de ruido delante de una decisión que el usuario ya sabe tomar mejor.

Paralelamente, el checklist canónico §2.5 exige peer context para contextualizar el moat — no tenerlo significa que el Value Expert razona sobre ROIC en el vacío.

## Decision

1. **Peers user-supplied en Checkpoint 1.** Entre el summary de especialistas y la confirmación para proceder, el CLI pregunta con una sola línea que hace **explícito que el skip es una opción legítima, no una degradación**:

   ```
   Peer tickers (comma-separated, e.g. MSFT,GOOGL,AMZN) — press Enter to skip (moat analysis will use 10-K only)
   ```

   La frase final entre paréntesis es obligatoria en el prompt: comunica al usuario que saltarse los peers NO rompe el análisis, solo acota la §2 Moat a fuentes internas del 10-K (management discussion, competitive analysis sin benchmark cuantitativo de ROIC).
2. **Nuevo nodo `peers_node`** en el grafo, entre `checkpoint1` y `checkpoint2`. Itera sobre `state["peers"]` y para cada uno invoca `EdgarAdapter.fetch_raw + parse_financials` (reuso exacto del Financials agent, sin nueva superficie de tooling).
3. **Poblado de `peer_roic_comparison`** en `competitive_agent` leyendo `state["peer_financials"]`. Cada entrada se tipa como `PeerROIC(ticker, roic, period)` — nuevo modelo Pydantic en `schemas/reports.py`.
4. **Degraded mode**:
   - Input vacío → `peers=[]`, el reporte dice explícitamente "Peer comparison skipped by user".
   - Un peer que falla (XBRL corrupto, ticker inválido) → se loguea warning y se omite; el resto de peers continúa. Fail-loud por peer, no por batch.
5. **Render en archiver**: nueva sección `## Peer Comparison — ROIC` con tabla `| Ticker | ROIC | Period |`.

## Consequences

- **+** Peer set de mayor calidad (Eduardo conoce los competidores reales).
- **+** Encaja con el patrón Socratic HITL (ADR-006): la pregunta misma tiene valor didáctico ("¿contra quién compite realmente AAPL ahora mismo?").
- **+** Zero nueva superficie de API — reuso puro de `EdgarAdapter`.
- **+** El Value Expert gana contexto cuantitativo para §2 (Moat) sin depender del LLM para inventar peers.
- **−** Añade ~15–30s por peer a la duración total del análisis (aceptable, son paralelizables en futuro).
- **−** Depende de que el usuario sepa sus peers. Mitigado: la pregunta sugiere 3–5 tickers típicos y permite skip.
- **−** El Competitive agent (Tavily) podría sugerir peers en su output — no los usamos automáticamente; el usuario puede inspirarse.

## Alternatives considered

- **SIC-based auto-discovery**: rechazado por ruido (ver §Context).
- **Embedding-based cluster discovery**: rechazado — requiere el corpus de ADR-013 primero, circular.
- **LLM-generated peer list**: rechazado — pedir al LLM que liste peers es exactamente el tipo de "LLM inventa datos" que ADR-005 prohíbe.
- **Ningún peer en Phase 2**: rechazado — el checklist §2.5 lo exige y el campo vacío llevaba 2 sesiones visible como "debt".

## Relación con otros ADRs

- **Extiende ADR-003**: añade un nodo post-checkpoint1 al patrón supervisor + especialistas.
- **Complementa ADR-006**: la pregunta de peers se integra al bloque Socratic de Checkpoint 1.
- **Reemplaza el caso "cross-ticker peer retrieval" que ADR-008 asumía**: ahora ese caso lo cubre user input, no RAG. Ver ADR-013.
- **Depende de ADR-011**: reuso de EDGAR XBRL como única fuente para el cálculo de ROIC de peers.
