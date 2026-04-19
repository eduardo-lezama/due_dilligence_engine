# ADR-011: Fuentes de datos US Fase 1

**Status**: Accepted
**Date**: 2026-04-19

## Context
Research 2026 revela:
- **`yfinance` está efectivamente roto**: rate limits agresivos (daily 429s), Yahoo bloquea scrapers impredeciblemente.
- **SEC EDGAR con XBRL** es la mejor fuente estructurada del mundo: gratis, sin auth, sin rate limits con la librería `edgartools`, cobertura completa de 10-K/10-Q/8-K/DEF 14A/Form 4/13F.
- Fuentes derivadas (FinancialModelingPrep, SimplyWallst) son útiles para convenience pero deben cross-check contra primary source.
- News APIs y earnings transcripts tienen alternativas free razonables.

Alineado con ADR-005 (primary-source-first), la elección de fuentes es crítica.

## Decision
### Primary sources Fase 1
| Tipo de dato | Fuente | Librería | Tier |
|---|---|---|---|
| Filings + XBRL financials | **SEC EDGAR** | `edgartools` | Primary authoritative, free, zero limits |
| Form 4 insider / 13F institutional | SEC EDGAR | `edgartools` + fallback `sec-api.io` (free 100/day) | Primary |
| Precios OHLCV | **Polygon.io** free tier (5 req/min, 2y histórico) | `polygon-api-client` | Derived clean |
| Transcripts earnings calls | **Finnhub** free tier | `finnhub-python` | Derived |
| Ratios derivados | **calculados inhouse** desde XBRL | — | Derived (controlled) |

### Fallbacks
- Precios: **Alpha Vantage** free (25 req/day) como fallback de Polygon.
- Filings: directo a `https://www.sec.gov/` HTTP download si `edgartools` falla.

### Prohibidos en Fase 1
- `yfinance`, `yahooquery`.
- Scraping de Yahoo Finance, Morningstar, SimplyWallst, Seeking Alpha (fragilidad + ToS).

### Fase 2+ (opcional)
- `FinancialModelingPrep` free (250/day) como convenience para ratios pre-calculados — solo si cross-check vs XBRL.
- News API (NewsAPI free o GDELT) para el Market & News Agent.
- Evaluar **OpenBB Platform** en Fase 3 como aggregator unificado cuando haya ≥3 proveedores.

## Consequences
- **+** Números autoritative by design.
- **+** Zero dependencia en proveedores frágiles.
- **+** Rate limits tolerables (cache agresivo mitiga).
- **−** Polygon free solo 2 años histórico — para análisis de 10 años necesitamos cache persistente o upgrade ($5-15/mo) en Fase 2-3.
- **−** `edgartools` requiere entender el modelo de XBRL (invariante en ADR-005).

## Gotchas blindadas
- **XBRL tagging inconsistencies** entre años: `pydantic` valida + cross-check contra texto 10-K si anomalía.
- **Restatements** (10-K/A): detectar Form 8-K Item 4.02 antes de usar histórico.
- **Form 4 lag**: se filea dentro de 2 días post-trade, pero visibilidad API puede tardar 24h más.
- **13F lag**: 45 días post-quarter-end.
- **Licensing/ToS**: todas las fuentes listadas son free para uso personal/no-comercial.

## Alternatives considered
- **Solo `yfinance`**: rechazado (roto).
- **Solo `sec-api.io` paid**: rechazado para Fase 1 (evitar subscriptions).
- **Bloomberg / Refinitiv**: fuera de budget personal, overkill.
