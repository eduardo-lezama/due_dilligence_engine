# Phase 2 Strategic Review

**Status:** Approved
**Date:** 2026-04-21
**Owner:** Eduardo (@eduardo-lezama)
**Scope:** post-audit correction + Phase 2 implementation order

---

## 1. Executive summary

Phase 1 shipped end-to-end el 2026-04-21 tras dos corridas reales en producción (AAPL, FSLR). Antes de arrancar Phase 2 hicimos una auditoría estratégica sobre un primer output del Plan agent. La auditoría corrigió varios supuestos que habrían contaminado Phase 2. Este documento es el registro de esas correcciones y el scope fijado para Phase 2.

Phase 2 entrega **seis ítems** en tres bloques de prioridad. No hay RAG. No hay NewsAPI. No hay peer-discovery heurístico. El trigger de RAG cambia y se resuelve en Phase 2.5 o Phase 3. El swap `MemorySaver → SqliteSaver` se **difiere a Phase 5** (webapp) por bug conocido de serialización msgpack-Pydantic — ver ADR-020.

## 2. Correcciones de auditoría (inmutables — no re-debatir)

### 2.1 El hallazgo "Tim Cook stepping down" NO era alucinación
El reporte de AAPL citaba una noticia de 2026-04-20 sobre John Ternus reemplazando a Tim Cook. La verificación por WebSearch confirmó que la noticia era real. El sistema capturó correctamente una señal de breaking news a través del canal Finnhub/Tavily.

- **Veredicto inicial (Plan agent):** alucinación crítica — el canal de noticias saltaba validación.
- **Veredicto corregido:** funciona según diseño; lo único que falta es que el markdown final atribuya los claims de noticias a la URL de donde salieron. Esto es una **mejora de transparencia**, no un bug.

### 2.2 El trigger de RAG estaba mal — no son ≥10 análisis, son ≥3 del mismo ticker
ADR-008 difería RAG hasta tener "≥10 análisis archivados". Ese trigger asumía que el caso principal era comparación cross-ticker de peers (ahora cubierto por peer list user-supplied — ver §2.4). La señal real de RAG viene de **re-análisis del mismo ticker** — Eduardo usa el sistema para gestionar una cartera de ~15 nombres con refresh trimestral/semestral. Comparar el análisis de AAPL hoy contra el anterior da un delta accionable (¿se está erosionando el moat? ¿la tesis sigue intacta? ¿los supuestos de WACC siguen siendo válidos?).

- **Trigger corregido:** ≥3 análisis del mismo ticker.
- **Sigue diferido** de Phase 2 — aterriza en Phase 2.5 o Phase 3. Ver ADR-013.

### 2.3 NewsAPI / GDELT descartados
El plan inicial consideraba añadir NewsAPI free o GDELT como fuente adicional de noticias para el Market agent. Rechazado:
- Tavily ya cubre búsqueda web cualitativa.
- Finnhub ya cubre noticias estructuradas por company tag.
- Lo que realmente falta es **event tracking primary-source del SEC** — filings 8-K con Items 2.02 (earnings), 4.02 (restatements), 5.02 (cambios de officers). Estructurado, legalmente autoritativo, y gratis vía EDGAR.
- Ver ADR-017.

### 2.4 Peer ROIC comparison: user-supplied en Checkpoint 1
El plan inicial proponía descubrir peers automáticamente por SIC code. Rechazado:
- Los SIC codes son ruidosos (AAPL y una recicladora de chatarra pueden caer en el mismo grupo de 4 dígitos).
- Eduardo conoce sus peers mejor que cualquier heurística.
- Preguntar en Checkpoint 1 encaja con el patrón Socratic HITL existente (ADR-006) — añade **valor de aprendizaje** (Eduardo se fuerza a articular "¿contra quién compite realmente AAPL ahora mismo?").
- Más simple de implementar. Mayor calidad. Ver ADR-015.

## 3. Scope aprobado para Phase 2

### Prioridad 1 — Foundation fixes (transparencia + UX)

1. **Citations + atribución de noticias en el markdown report.** `archiver._render_markdown` debe renderizar `state["citations"]` en una sección "Sources & Citations", y los claims de noticias deben llevar las URLs de Finnhub/Tavily inline en las secciones de Risks y Market. Extiende ADR-005.

### Prioridad 2 — Nuevas fuentes de datos (alimentar mejor al Expert)

2. **Peer ROIC comparison (user-supplied).** Preguntar al usuario en Checkpoint 1 por 3–5 peer tickers. Para cada uno, correr `financials_agent` (reuso del existente). Renderizar `CompetitiveReport.peer_roic_comparison` como tabla en el reporte final. Skip explícito permitido con Enter vacío. Ver ADR-015.

3. **Earnings call transcripts (Finnhub).** Añadir `FinnhubAdapter.fetch_transcripts(ticker, quarters=4)`. Rutear a través de `management_agent` (la sección Q&A es la señal primaria de candor del management). Ver ADR-016.

4. **8-K event scanning (EDGAR).** Añadir `EdgarEventsAdapter.fetch_8k_events(ticker, items=[...], months=12)`. Rutear a través de `market_agent` como bloque separado "Material events". Ver ADR-017.

### Prioridad 3 — Calidad de síntesis

5. **Value Expert prompt v2 + contexto histórico reverse-DCF.** Reescribir `_SYSTEM_PROMPT` en `value_expert.py` de "rellenar 9 secciones" a "integrar y adjudicar señales conflictivas". Inyectar CAGR histórico de revenue a 5 años para que el Expert contraste explícitamente el implied growth vs la historia. Ver ADR-019.

6. **Checkpoint 1+2 Socratic real.** Reemplazar `typer.confirm("Proceed?")` con prompts Socratic anclados en datos. Checkpoint 1 formula una pregunta por señal crítica (evolución del moat, cambios de management, concentración de riesgo). Checkpoint 2 muestra CAGR histórico vs WACC/growth propuestos y pide justificación al usuario. Ver ADR-018.

## 4. Fuera de scope para Phase 2 (tracked)

- DCF base FCF es snapshot de un solo año (sin normalización de owner earnings). Limitación reconocida. Phase 3.
- El Supervisor node está bien — no tocar.
- `valuation._fcf_cagr` cae silenciosamente a 7% cuando el FCF más antiguo ≤ 0. Añadir `log.warning` pero no rediseñar. Track para Phase 2.5.
- Comparación moat cross-ticker más allá de ROIC. Phase 3.
- **SqliteSaver checkpointer** (swap `MemorySaver → SqliteSaver` en CLI). Diferido a Phase 5 por bug conocido de serialización msgpack con modelos Pydantic v2 en el state. El archiver ya persiste markdown + SQLite, cubriendo el caso real de uso. Ver ADR-020.

## 5. Mapa de ADRs para Phase 2

| ADR | Título | Relación |
|---|---|---|
| ADR-013 | RAG trigger redefinido a re-análisis same-ticker | **Supersede** ADR-008 |
| ADR-014 | Visibilidad de citas y atribución de noticias en reportes | **Extiende** ADR-005 |
| ADR-015 | Metodología de peer comparison | Nuevo |
| ADR-016 | Earnings call transcripts como primary source | **Extiende** ADR-011 |
| ADR-017 | 8-K event tracking desde EDGAR | **Extiende** ADR-011 |
| ADR-018 | Socratic checkpoints v2 | **Extiende** ADR-006 |
| ADR-019 | Value Expert prompt v2 — síntesis sobre rúbrica | **Extiende** ADR-009 |
| ADR-020 | SqliteSaver checkpointer diferido a Phase 5 | Nuevo (documenta deferral) |

## 6. Definition of Done para Phase 2

Todos los ítems siguientes deben pasar antes de declarar Phase 2 completa:

1. Una nueva corrida de `uv run due-diligence analyze AAPL` produce un reporte markdown que contiene (a) un apéndice "Sources & Citations" con ≥5 URLs únicas de Finnhub/Tavily/EDGAR y (b) al menos un claim de noticia con atribución de URL inline.
2. La sección `## Peer Comparison` del reporte contiene 3–5 peer tickers con ROICs reales extraídos de XBRL **o** una línea explícita "Peer comparison skipped by user" si el usuario pulsó Enter en el prompt de peers.
3. El `§3 Management Assessment` del Expert cita al menos un excerpt textual de un transcript de Finnhub, con URL del transcript en la cita.
4. El reporte incluye una tabla "8-K Events (last 12 months)" con ≥1 fila, o una fila explícita "No material 8-K events" (fail-loud, no silencioso).
5. El párrafo `§6 Valuation` del Expert compara explícitamente el implied growth del reverse-DCF contra el CAGR histórico de revenue a 5 años.
6. El output de Checkpoint 1 en el CLI contiene al menos una pregunta Socratic formulada con signo de interrogación, anclada en datos reales del state.

## 7. Lo que explícitamente NO hicimos

- **No** añadimos ChromaDB, embeddings ni ningún vector store. RAG diferido.
- **No** añadimos NewsAPI ni GDELT.
- **No** cambiamos el Supervisor ni el Financials agent.
- **No** introducimos heurísticas de peer discovery.
- **No** tocamos el patrón fan-out de LangGraph.
- **No** reescribimos el Critic — está funcionando.
- **No** hicimos el swap `MemorySaver → SqliteSaver` — diferido a Phase 5 por bug conocido de serialización (ADR-020).

## 8. Timeline objetivo

Aproximadamente 3–4 sesiones interactivas de pacing Modo Profesor (1 task por sesión de media). El plan en `docs/superpowers/plans/2026-04-21-phase-2.md` dirige la ejecución. Ningún ítem arranca sin un checkpoint didáctico previo.
