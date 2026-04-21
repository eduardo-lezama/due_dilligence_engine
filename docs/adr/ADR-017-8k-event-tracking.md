# ADR-017: 8-K event tracking desde EDGAR (sustituye a news APIs agregadores)

**Status**: Accepted
**Date**: 2026-04-21
**Extends**: [ADR-011](ADR-011-us-data-sources.md)

## Context

Phase 1 tiene un único gap news-like que no cubre: **eventos materiales entre filings 10-K**. Entre dos reportes anuales pasan 12 meses durante los cuales pueden ocurrir:
- Resultados trimestrales (guidance, earnings misses/beats)
- Restatements de estados financieros (**CRÍTICO** — señal de baja calidad contable)
- Cambios de oficiales (CEO, CFO)
- Cambios de auditor
- M&A significativas

El plan inicial consideraba añadir **NewsAPI free** o **GDELT** para cubrir este gap. Rechazado:
- Son agregadores → frágiles ante cambios de fuente.
- Ruido alto (mezclan señal con clickbait).
- No son fuente primaria → cualquier claim derivado hereda la fragilidad del agregador.

**La señal primaria existe**: los filings **8-K** del SEC son reportes obligatorios de eventos materiales, con **Item numbers estructurados** (2.02 earnings results, 4.02 non-reliance on previously issued financials → restatement, 5.02 officer departure/appointment). Son legalmente autoritativos, gratis, y `edgartools` ya los expone.

ADR-011 mencionó 8-K como "futura fase". Este ADR lo lanza en Phase 2 como parte del Market agent.

## Decision

1. **Nueva clase `EdgarEventsAdapter`** en `tools/edgar.py` (paralela a `EdgarAdapter` y `EdgarFilingsAdapter`) con método `fetch_8k_events(ticker, items=[...], months=12)`.
2. **Items por defecto**: `["2.02", "4.02", "5.02"]` — los tres tipos con mayor señal investor-relevant. Parámetro para extender si hace falta.
3. **Output shape**: `list[dict]` con `{filing_date, item_numbers, summary_text, filing_url}`. Summary truncado a 600 chars (no deep-parse — el objetivo es señalizar, no narrar).
4. **Schema extension**: nuevo campo `material_events: list[dict] = Field(default_factory=list)` en `MarketReport`.
5. **Integración en `market_agent`**: al final del node, pobla `material_events`. No genera summary LLM — los datos son estructurados.
6. **Render en archiver**: nueva sección `## Material Events (8-K, last 12 months)` con tabla `| Date | Item(s) | Summary | Source |`. Si no hay eventos, una fila explícita "No material 8-K events in the last 12 months" (fail-loud, no omisión silenciosa).
7. **Detección especial de Item 4.02**: el Value Expert prompt v2 (ADR-019) incluirá regla: un 4.02 reciente debe pesar como red flag mayor — alinea con ADR-011 gotcha sobre restatements.

## Consequences

- **+** Señal estructurada y legalmente autoritativa — eliminamos la rabbit hole de news aggregators.
- **+** Item 4.02 detection automatizada es una mitigación directa del gotcha de ADR-011 (restatements silenciosos).
- **+** Complementa Finnhub news (unstructured/qualitative) con eventos SEC structured — cobertura completa.
- **+** Zero nueva dependencia — `edgartools` ya expone 8-K.
- **−** El summary de 8-K es heterogéneo (cada empresa formatea distinto) — solo guardamos snippets cortos.
- **−** Item 4.02 en particular requiere parsing del texto para el contexto completo; guardamos el summary y la URL para que el Expert (o el lector) profundice.
- **−** Un ticker sin 8-K en 12 meses no es señalable — la fila "No material 8-K events" es por transparencia, no por valor.

## Alternatives considered

- **NewsAPI free**: rechazado — agregador frágil, señal inferior.
- **GDELT**: rechazado — ruido alto, tuning excesivo para este uso.
- **SEC-API.io**: rechazado — tier paid para filtrado por Item.
- **Scraping directo de SEC full-text**: rechazado — `edgartools` ya está en el stack y cubre esto.
- **Solo Item 4.02** (mínimo viable): rechazado — 2.02 y 5.02 son tan importantes como 4.02 para un analista value.

## Relación con otros ADRs

- **Extiende ADR-011**: añade 8-K como fuente primaria secundaria al lado del 10-K.
- **Mitiga gotcha de ADR-011** sobre restatements (ver `docs/adr/ADR-011-us-data-sources.md`).
- **Alimenta ADR-019** (Value Expert prompt v2): el Expert debe considerar 8-K events en §7 Risks.
- **Renderizado por ADR-014**: las URLs de 8-K entran al apéndice `## Sources & Citations`.
