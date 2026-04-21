# ADR-014: Visibilidad de citas y atribución de noticias en reportes

**Status**: Accepted
**Date**: 2026-04-21
**Extends**: [ADR-005](ADR-005-hallucination-control-by-architecture.md)

## Context

ADR-005 exige que todo claim cualitativo emita un `Citation{claim, source_url, excerpt, confidence}` validado por substring-match contra el filing cacheado. Phase 1 cumple la captura: los agentes LLM pueblan `state["citations"]` correctamente. **Lo que falta es la visibilidad.**

El `archiver._render_markdown` actual nunca renderiza `state["citations"]`. El lector del reporte ve prosa sintetizada sin forma de auditar el trail hacia fuentes primarias. El episodio AAPL "Tim Cook → John Ternus" (claim real, verificado a posteriori por WebSearch) expuso el problema: el sistema había capturado la fuente correctamente pero el markdown final no atribuía la URL de la noticia, por lo que para un lector casual el claim se veía indistinguible de una alucinación.

Un citation trail invisible falla al objetivo de ADR-005 incluso cuando la captura funciona — porque la auditabilidad depende del artefacto final, no de un campo interno del state.

## Decision

1. **Nuevo bloque obligatorio en el reporte**: `## Sources & Citations` al final del markdown, agrupado por agente (Management, Filings 10-K, Competitive & Moat). Dentro de cada grupo:
   - `- **<claim>** (confidence: <pct>%)`
   - `  > <excerpt verbatim>`
   - `  Source: [<url>](<url>)`
2. **Atribución inline de noticias**: los claims que derivan de Tavily o Finnhub surface la URL directamente en la sección del cuerpo (p.ej. en `§7 Risks` una frase sobre noticia termina con `Source: [Reuters](<url>)`). Esto complementa al apéndice, que es el trail auditable completo.
3. **Confidence visible**: cada cita muestra su score (0.0–1.0) para que el lector pueda filtrar mentalmente claims de baja confianza.
4. **Stub skip**: si un reporte del agente es stub (no corrió por algún motivo), su sección se omite — no aparece vacía.
5. **Implementación**: nueva función `_render_citations_section(state) -> list[str]` en `archiver.py`, llamada antes del footer.

## Consequences

- **+** Auditabilidad completa del reporte — cualquier claim puede ser verificado clickando la URL.
- **+** El caso John-Ternus (y similares) deja de ser opaco: el lector ve la fuente y la confidence sin re-correr el análisis.
- **+** Fuerza disciplina en el canal de noticias: si Finnhub/Tavily no devuelven URL, el claim pierde visibilidad (incentivo correcto para los adapters).
- **+** Prepara el reporte para revisiones regulatorias o auditorías (cada afirmación con su fuente).
- **−** Los reportes markdown crecen en longitud (aceptable).
- **−** Requiere que los agentes LLM poblen `citations` con URL válidas (ya lo hacen en Phase 1, pero el QA del apéndice lo evidenciará).

## Alternatives considered

- **JSON sidecar** (`<TICKER>-<DATE>.citations.json` al lado del markdown): rechazado. La auditabilidad debe vivir en el artefacto primario, no en un archivo separado que nadie abre.
- **Comentarios HTML ocultos en el markdown**: rechazado. "Escondido" no es "auditable".
- **Archivo `.citations.md` separado**: misma razón que JSON sidecar.
- **Solo atribución inline sin apéndice**: rechazado. Las citaciones largas rompen la legibilidad de la prosa; apéndice + inline es el mejor compromiso.

## Relación con otros ADRs

- **Extiende ADR-005**: completa el ciclo captura → validación → **visibilidad** del trail.
- **Complementa ADR-007**: los reportes markdown archivados ahora contienen el trail completo en sí mismos, no solo en state.
- **Habilita ADR-016 y ADR-017**: transcripts y eventos 8-K serán otra fuente adicional de URLs que este renderer expondrá automáticamente.
