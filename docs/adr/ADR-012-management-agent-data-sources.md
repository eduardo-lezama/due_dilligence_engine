# ADR-012: Fuentes de datos del Management Agent

**Status**: Accepted
**Date**: 2026-04-20

## Context

El Management Agent cubre la sección §3 del checklist canónico (Management & Skin in the Game).
Las preguntas de §3 son mayoritariamente cualitativas: ¿el CEO tiene alineación con accionistas?
¿Cuánto tiempo lleva? ¿Cómo compensa al equipo? ¿Asigna capital inteligentemente?

Estas preguntas **no se responden desde los estados financieros** (income statement, balance sheet,
cash flow). Los financials muestran los *resultados* de la gestión, no las *características* de los
gestores. Usar solo datos financieros sesgaría el análisis hacia lo cuantitativo y perdería la
mitad del diagnóstico.

ADR-011 ya lista las fuentes permitidas para Fase 1. Este ADR especifica cuáles aplican al
Management Agent y añade Tavily como fuente de búsqueda web para contexto cualitativo.

## Decision

### Fuentes del Management Agent (en orden de prioridad)

| Fuente | Filing / API | Qué responde del §3 | Tier |
|---|---|---|---|
| **SEC EDGAR — DEF 14A** | Proxy statement anual | Compensación ejecutiva, % ownership de insiders, say-on-pay votes, composición del board, independencia de directores | Primary authoritative |
| **SEC EDGAR — Form 4** | Insider transaction filings | Compras/ventas recientes de acciones por CEO/CFO/directores — señal directa de skin in the game | Primary authoritative |
| **SEC EDGAR — 10-K Item 10** | Annual report, sección de management | Bios de directores y officers: formación, historial en otras empresas, años en el cargo, otros boards actuales | Primary authoritative |
| **Tavily Search** | Web search API | Contexto cualitativo: entrevistas, artículos de fondo sobre el CEO, noticias recientes, controversias, reputación en el sector | Derived / contextual |

### Por qué Tavily (y no otra opción)

- **OpenAI web_search_preview**: existe en la Responses API pero requiere reescribir el wrapper
  del LLM client (cambia el formato de llamada) y acopla la búsqueda al proveedor.
- **Finnhub / Polygon news**: cubren noticias financieras recientes pero no búsqueda semántica
  libre — útiles para el Market Agent, no para investigación de management.
- **Tavily**: API diseñada para agentes LLM. Devuelve fragmentos limpios con URL, título y
  snippet — directamente convertibles a `Citation`. Free tier 1.000 créditos/mes, suficiente
  para uso personal. Provider-agnóstico respecto al LLM.

### Patrón de uso en el agente

1. Fetch DEF 14A, Form 4 (últimos 12 meses), 10-K Item 10 via `edgartools` → datos estructurados
2. Tavily search con queries dirigidas: `"{ticker} CEO compensation alignment"`,
   `"{ticker} management capital allocation history"`, etc. → contexto cualitativo
3. Todo el material se entrega al LLM como contexto. El LLM interpreta, no genera números.
4. Cada claim del output lleva `Citation` con `source_url` apuntando al filing o URL de búsqueda.

### Queries Tavily recomendadas para §3

- `"{company_name} CEO tenure track record"`
- `"{company_name} insider ownership buybacks"`
- `"{company_name} management capital allocation"`
- `"{company_name} CEO compensation shareholder alignment"`

## Consequences

- **+** Cobertura completa del §3 con primary sources verificables (DEF 14A, Form 4, 10-K §10).
- **+** Tavily añade el contexto cualitativo que los filings no capturan.
- **+** Zero riesgo de alucinación numérica: los números (ownership %, compensation $) vienen
  de EDGAR XBRL o texto de filing, nunca del LLM.
- **+** Tavily free tier cubre el uso personal sin coste adicional.
- **−** Tavily añade una dependencia externa nueva (API key extra en `.env`).
- **−** Los resultados de Tavily son menos deterministas que EDGAR — la calidad varía según la
  empresa. Para empresas pequeñas o poco cubiertas, la búsqueda puede devolver poco.
- **−** DEF 14A parsing via `edgartools` puede requerir trabajo adicional si el formato varía
  entre empresas (especialmente small caps).

## Alternatives considered

- **Solo financials + LLM inference**: rechazado. El LLM inferir management quality desde
  ratios introduce exactamente el sesgo que ADR-005 prohíbe.
- **Finnhub transcripts para earnings calls**: válido como fuente adicional en Fase 2. Diferido
  para no over-engineer el agente en Fase 1b.
- **OpenAI web_search_preview**: diferido. Requiere cambio en el LLM wrapper que merece su
  propio ADR cuando la necesidad sea más amplia que un solo agente.

## Relación con otros ADRs

- Extiende **ADR-011** (US data sources) añadiendo Tavily como fuente permitida para agentes.
- Respeta **ADR-005** (zero hallucination): todos los datos numéricos vienen de EDGAR.
- Respeta **ADR-004** (provider abstraction): Tavily es independiente del proveedor LLM.
