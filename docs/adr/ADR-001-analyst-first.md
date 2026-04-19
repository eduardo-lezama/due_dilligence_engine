# ADR-001: Analyst-first, screener deferred

**Status**: Accepted
**Date**: 2026-04-19

## Context
El objetivo del sistema combina dos casos de uso distintos:
- **Descubrir** empresas infravaloradas (screening sobre un universo amplio).
- **Analizar** en profundidad una empresa concreta (deep-dive dado un ticker).

Screening requiere ingesta masiva periódica, filtrado cuantitativo y un universo de datos. Deep-dive es on-demand, intensivo por empresa y de alto valor por análisis. Mezclarlos en Fase 1 infla scope y difumina el valor que aporta el LLM (razonamiento cualitativo sobre narrativa, moats, management), que es donde está la palanca en análisis.

## Decision
Fase 1 implementa **solo el analyst** (input = ticker → output = report completo). El screener se evalúa en Fase 4 como módulo separado, considerando primero apoyarse en servicios externos gratuitos (FinViz, Koyfin free tier) antes de construirlo inhouse.

## Consequences
- **+** Scope reducido y alcanzable para un MVP personal.
- **+** El analyst es el núcleo de valor; acelerarlo no bloquea nada.
- **−** El usuario debe generar candidatos manualmente (prensa, foros, newsletters value) en Fases 1-3.
- Datos y adapters construidos para el analyst son reutilizables por el screener, no hay throwaway.

## Alternatives considered
- **Ambos desde día 1**: rechazado por scope.
- **Solo screener**: rechazado — menor valor diferencial frente a herramientas ya existentes, y screening basado en LLM tiene menos palanca que la síntesis cualitativa.
