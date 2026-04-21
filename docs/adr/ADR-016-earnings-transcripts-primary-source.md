# ADR-016: Earnings call transcripts como primary source del Management Agent

**Status**: Accepted
**Date**: 2026-04-21
**Extends**: [ADR-011](ADR-011-us-data-sources.md), [ADR-012](ADR-012-management-agent-data-sources.md)

## Context

ADR-012 listó las fuentes del Management Agent para Phase 1b: DEF 14A + Form 4 + 10-K §10 + Tavily. Funciona, pero tiene un agujero conocido:

- **DEF 14A** cubre compensación anual (structure, targets) — es retrospectiva y no refleja tono.
- **Form 4** cubre transacciones insider — señal cuantitativa pero silenciosa sobre el "por qué".
- **10-K §10** cubre bios estáticas del board y officers.
- **Tavily** añade contexto cualitativo pero es secundario (artículos, no fuente primaria).

**Lo que falta** es la señal más rica sobre management: **candor en tiempo real**. Las earnings calls trimestrales — especialmente la sección Q&A — son donde el management responde sin guión a preguntas incómodas. Ahí se evidencian:
- Claridad sobre capital allocation (§3.4 del checklist).
- Filosofía de compensación (§3.3) revelada a menudo en respuestas a analystas activistas.
- Transparencia: ¿el CEO responde con números concretos o con evasivas genéricas?
- Tono ante guidance cuts o misses.

ADR-012 mismo contemplaba añadir transcripts como extensión en Phase 2. Este ADR lo formaliza.

## Decision

1. **Nuevo método `FinnhubAdapter.fetch_transcripts(ticker, quarters=4) -> list[dict]`** en `tools/finnhub.py`. Por cada transcript devuelve `{id, year, quarter, date, prepared_remarks, qa, source_url}`.
2. **Separación prepared remarks vs Q&A** por heurística de texto (buscar "Q&A" o "question-and-answer" en el turno). La Q&A es la señal de mayor valor — el prompt del agente lo explicita.
3. **Integración en `management_agent`**: bloque `## SOURCE 5: Earnings Call Transcripts` inyectado entre SOURCE 4 (Tavily) y las instrucciones. Helper `_format_transcripts` para prepararlo.
4. **Citaciones**: claims derivados de transcripts usan `source_url = "Finnhub transcript <ticker> <period>"` y copian el excerpt verbatim del speaker. Regla nueva en el RULES del system prompt del agente.
5. **Degraded mode**: sin API key de Finnhub → `[]` + warning log; si un transcript específico falla, se omite sin bloquear los demás.
6. **Tier**: Finnhub free tier es suficiente (transcripts incluidos, 60 calls/min).

## Consequences

- **+** Cubre §3.3 (filosofía de compensación) y §3.4 (capital allocation track record) con señal fresca trimestral, no anual.
- **+** El Expert puede cuotar frases textuales en §3 Management Assessment — añade credibilidad al output final.
- **+** Complementa Form 4 (cuánto) con transcript Q&A (por qué) para skin-in-the-game.
- **+** Zero nueva dependencia — Finnhub ya existe en el stack (ADR-011/012).
- **−** Los Q&A son texto ruidoso — el prompt debe instruir al LLM a preferir excerpts citables, no parafrasear.
- **−** Lag típico de ~48h post-call (aceptable; el sistema no es para trading de eventos).
- **−** El volumen de texto por call (~10k tokens prepared + ~15k Q&A) engorda el prompt del management agent. Mitigado con truncado a 2000/3000 caracteres por bloque.

## Alternatives considered

- **Motley Fool transcript scraping**: rechazado — viola ToS y no es fuente primaria.
- **Seeking Alpha API**: rechazado — paywall.
- **Transcripción desde YouTube audio con AssemblyAI / Whisper**: rechazado — over-engineering para un MVP cuando Finnhub free ya resuelve.
- **Dejar transcripts fuera de Phase 2 (otra vuelta)**: rechazado — el gap en §3 es visible en los reportes reales de AAPL/FSLR.

## Relación con otros ADRs

- **Extiende ADR-012**: añade SOURCE 5 a la trifecta EDGAR + Tavily.
- **Respeta ADR-005**: ninguna cifra se genera — excerpts son texto verbatim con URL identificable.
- **Respeta ADR-014**: los transcripts serán citados con URL y excerpt en el apéndice `## Sources & Citations`.
