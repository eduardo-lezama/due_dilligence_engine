# ADR-002: Mercados por orden US → CAD → EU

**Status**: Accepted
**Date**: 2026-04-19

## Context
Distintos mercados requieren distintas fuentes de filings, formatos de reporting, tratamiento de idiomas y de divisas:
- **US (NYSE/NASDAQ)**: SEC EDGAR con XBRL obligatorio y estructurado, gratis, sin rate limits vía `edgartools`. Todo en inglés. Mejor cobertura de APIs auxiliares (precios, transcripts, insider filings).
- **CAD (TSX)**: SEDAR+ con API y filings (MD&A, AIF) — menos rico que EDGAR pero manejable.
- **EU**: fragmentado (ESMA centraliza solo parcialmente; CNMV, AMF, FCA, BaFin cada uno con su portal). Filings en idioma local frecuente. Monedas distintas. IFRS vs US GAAP.

El usuario invierte principalmente en US, progresivamente extendiendo a CAD y EU.

## Decision
1. **Fase 1**: solo US.
2. **Fase 3**: añadir adapters **CAD** (SEDAR+) y **EU** (prioridad UK FCA + España CNMV inicialmente).
3. La arquitectura debe ser **polimórfica desde Fase 1**: interfaces `FilingsProvider`, `PricesProvider`, `TranscriptsProvider` permiten añadir mercados como extensión, no reescritura.
4. Handling multi-idioma vía LLM con flag explícito de traducción. Handling multi-currency con `currency_code` obligatorio en toda `MetricValue`.

## Consequences
- **+** Fase 1 arranca rápido con la mejor fuente del mundo (SEC EDGAR).
- **+** Las interfaces polimórficas desde Fase 1 son baratas (design time), pero habilitan Fase 3 sin pagar deuda.
- **−** Un usuario que busque EU en Fase 1-2 no lo tiene.

## Alternatives considered
- **US + EU paralelos desde Fase 1**: rechazado por fragmentación EU que ralentiza el MVP.
- **Diseñar Fase 1 sin interfaces polimórficas**: rechazado — cuesta poco hacerlas ahora, mucho refactorizarlas después.
