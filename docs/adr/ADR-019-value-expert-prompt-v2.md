# ADR-019: Value Expert prompt v2 — síntesis sobre rúbrica

**Status**: Accepted
**Date**: 2026-04-21
**Extends**: [ADR-009](ADR-009-verdict-plus-analysis.md)

## Context

ADR-009 definió el output del Value Expert como 9 secciones estructuradas + VERDICT. Phase 1 implementó un `_SYSTEM_PROMPT` que instruye al LLM a "rellenar 9 secciones, cada una 3–4 frases". Funciona, pero la revisión post-hoc de los reportes reales de AAPL y FSLR expuso un patrón problemático:

**El Expert parafrasea los reportes de especialistas en lugar de integrarlos y adjudicar conflictos.**

Ejemplos observados:
- §6 Valuation de FSLR dice "DCF muestra 28% upside" — ni contrasta contra implied growth de 3.7% ni contra el CAGR histórico. Es un dato, no una síntesis.
- Cuando Competitive dice moat "narrow" pero Financials muestra ROIC 25%, el Expert no nota el conflicto ni lo adjudica. Elige una u otra sin razonar.
- §9 Decision Frame queda en lugares comunes ("monitor execution") sin nombrar condiciones concretas de thesis-breaker.

El problema es del prompt: "rellenar rúbrica" incentiva summary-por-sección en lugar de integration cross-sections.

Adicionalmente, el reverse-DCF implied growth se calcula en Valuation pero **no se contrasta con el CAGR histórico de revenue** en ningún punto del pipeline — ni el Expert tiene ese número disponible. Sin ese contraste, el implied growth es un dato sin contexto.

## Decision

### Nuevo prompt v2 — principios

1. **Declaración de intent**: "Your task is NOT to summarize — it is to INTEGRATE and ADJUDICATE conflicting signals."
2. **Regla dura de adjudicación**: "You MUST name at least 2 conflicting signals across the specialist reports and explain how you adjudicate them." Ejemplo en el prompt: "moat classified 'narrow' but ROIC=25% — I adjudicate as narrow because margin compression in MD&A overrides the trailing ROIC".
3. **§6 Valuation debe contrastar reverse-DCF vs historical CAGR**: "MUST explicitly contrast the reverse-DCF implied growth against the historical 5y revenue CAGR and say whether the market is pricing in expansion, contraction, or continuation."
4. **§9 Decision Frame con thesis-breaker concreto**: "MUST name the thesis-breaker condition as a concrete observation ('thesis breaks if gross margin prints below 35% for two consecutive quarters')."
5. **§7 Risks incluye 8-K events** (ADR-017), no solo 10-K Item 1A.
6. **§3 Management quota transcript Q&A** (ADR-016) cuando esté disponible.
7. **Data-scarcity discipline**: "If a section lacks sufficient data, say so explicitly rather than speculate."
8. **Mismos 9 keys de output** — el schema de `ValueExpertReport` no cambia. El schema queda, el razonamiento cambia.

### Historical CAGR injection

- Nuevo helper `_compute_revenue_cagr_5y(revenue_series)` en `agents/financials.py` — calcula CAGR 5y con fallback a span disponible si hay < 6 puntos. Retorna `None` si oldest ≤ 0 o < 2 puntos.
- Resultado se escribe a `state["historical_cagr_5y"]` (nuevo campo `NotRequired[float]` en `AnalysisState`).
- `_format_context` del Value Expert inyecta un bloque `## HISTORICAL CONTEXT` con el CAGR antes de llamar al LLM.

### VERDICT rubric refinada

El VERDICT sigue siendo BUY/HOLD/SELL, pero la rúbrica se explicita en el prompt:
- **BUY**: moat confirmado + quality management + DCF upside > 20% + sin 8-K red flags + conflictos resueltos hacia el bull case.
- **HOLD**: conflictos sin resolver, fair value, o data insuficiente.
- **SELL**: moat deteriorándose O value trap O DCF downside > 20% con red flags O 8-K Item 4.02 reciente.

## Consequences

- **+** El Expert produce síntesis real en lugar de summary.
- **+** El reverse-DCF implied growth se ancla en historia, no flota sin contexto.
- **+** Los thesis-breakers explícitos ayudan al Critic (ADR-009) a identificar condiciones verificables.
- **+** Schema unchanged → serialización downstream (archiver, SQLite) no se toca.
- **−** Prompt más largo (~+500 tokens). Coste marginal en Claude Sonnet — aceptable.
- **−** Más sensible a datos faltantes. El prompt lo aborda con regla explícita, pero puede producir más frases "insufficient data for §X".
- **−** Si Financials agent no puede calcular CAGR (serie corrupta), §6 pierde el anchor — se loguea y el prompt dice "historical CAGR unavailable".

## Alternatives considered

- **Multi-pass Expert** (first draft + self-critique en un segundo LLM call): rechazado por research de ADR-005 — añade coste y no demuestra mejora significativa en este caso de uso.
- **Swap a Claude Opus solo para el Expert**: diferido — el coste en corridas recurrentes es significativo y Sonnet con prompt v2 es suficiente.
- **Solo añadir el CAGR sin rewriting el prompt**: rechazado — el problema de parafraseo existe independientemente del dato histórico.
- **Structured reasoning forced** (chain-of-thought explícito en el prompt): descartado — rompe el output JSON puro y añade fragilidad al parsing.

## Relación con otros ADRs

- **Extiende ADR-009**: mismo VERDICT output shape, distinto razonamiento.
- **Respeta ADR-005**: todas las cifras siguen viniendo verbatim de los reportes especialistas; el Expert razona, no genera números.
- **Depende de ADR-016 (transcripts)**: §3 cita transcripts cuando disponibles.
- **Depende de ADR-017 (8-K events)**: §7 incorpora events estructurados.
- **Alimenta ADR-018 (Socratic v2)**: el CAGR histórico también viaja a Checkpoint 2 para anclar las asunciones.
