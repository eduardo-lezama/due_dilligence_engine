# CLAUDE.md — Due Diligence System

Instrucciones always-on a nivel de proyecto. Complementa — no reemplaza — las globales en `~/.claude/CLAUDE.md` (git, estilo, escalado de modelos, etc.).

---

## Invariantes no-negociables

Estos son principios arquitectónicos. Violarlos requiere discusión explícita y actualización del ADR correspondiente.

1. **Zero hallucination numérica** — toda cifra (revenue, FCF, ROIC, deuda, ratios, precios) sale de tools deterministas contra fuentes primarias (SEC EDGAR XBRL vía `edgartools`, Polygon, Finnhub). El LLM razona sobre ellas, no las genera. Ver [ADR-005](docs/adr/ADR-005-hallucination-control-by-architecture.md).

2. **Citas obligatorias y validadas** — todo claim cualitativo emite un `Citation{claim, source_url, excerpt, confidence, gaap_vs_nongaap_flag}` con Pydantic. Validación post-hoc por substring-match contra el filing cacheado. Claim sin cita válida → `confidence=0` y excluido de síntesis.

3. **Provider-agnostic** — los LLMs se acceden a través de `due_diligence.llm.client` con fallback Claude→OpenAI. Nunca importar `anthropic` u `openai` directamente desde código de negocio. Ver [ADR-004](docs/adr/ADR-004-provider-abstraction.md).

4. **Fuentes de datos en primary-source-first** — prohibido `yfinance` y scraping de Yahoo/Morningstar/SimplyWallst en Fase 1. Ver [ADR-011](docs/adr/ADR-011-us-data-sources.md).

5. **Tools fallan loud** — nunca fallback silencioso de "tool roto" a "LLM inventa número". Error explícito + log + aborto de rama si la métrica es crítica.

6. **Prompts de agentes alineados con el checklist canónico** — antes de escribir o modificar el prompt de cualquier agente especialista, leer la sección correspondiente de [`docs/checklist-value-investing.md`](docs/checklist-value-investing.md). Cada agente debe cubrir exactamente las preguntas de "su" sección (Management → §3, Filings → §1+§7, Competitive → §2, Valuation → §6, Value Expert → §8+§9). Un prompt que no cubre su sección equivale a un sistema que no hace lo que promete.

---

## Stack (Fase 0-1)

- Python 3.12, `uv` para dependency management
- LangGraph (state + interrupt + SQLite checkpointer)
- Pydantic v2 para schemas
- `edgartools` (SEC EDGAR XBRL — primary source), `polygon-api-client`, `finnhub-python`
- Anthropic SDK + OpenAI SDK (accedidos vía wrapper propio)
- `typer` + `rich` para CLI
- `structlog` para logging JSON
- pytest + fixtures cacheados (zero API calls reales en tests)

**Prohibido en Fase 0-1** (evita over-engineering): LangChain completo, Redis, Celery, Postgres, Docker, MCP servers, vector stores, observability SaaS, Ollama / modelos locales.

---

## Antes de arrancar una fase nueva o crear un directorio significativo

**OBLIGATORIO leer [`docs/mce-evolution.md`](docs/mce-evolution.md)** y ejecutar todos los ítems cuyo trigger se haya cumplido. Este archivo es la única fuente de verdad de estructura diferida (subdirectorios CLAUDE.md, skills, slash-commands, hooks).

Reglas:
- **Un hecho = un lugar**. Stack, invariantes y convenciones viven aquí. Decisiones viven en `docs/adr/`. Checklist de dominio en `docs/checklist-value-investing.md`. No duplicar.
- Si detectas duplicación, señalarla y proponer consolidar.
- Si detectas patrón repetible (p.ej. 2º especialista con estructura idéntica al 1º), proponer empaquetarlo como skill y anotarlo en `mce-evolution.md`.

---

## Context7 — documentación de librerías

Antes de asumir cualquier API de librería desde memoria de entrenamiento, consultar Context7:
1. Primero: servidor MCP hosteado `mcp__claude_ai_Context7__*` (resolve-library-id → query-docs)
2. Fallback si cuota agotada: servidor `context7-personal`

Aplicar siempre para: edgartools, LangGraph, Pydantic, Tavily, polygon-api-client, finnhub, typer, structlog, y cualquier librería con API que pueda haber cambiado desde el entrenamiento.

---

## Mapa de documentación del proyecto

| Archivo | Qué contiene | Cuándo leerlo |
|---|---|---|
| `README.md` | Roadmap, research, arquitectura, principios | Al entrar al proyecto |
| `docs/adr/` | Decisiones arquitectónicas numeradas | Al cuestionar una decisión o antes de cambiarla |
| `docs/adr/README.md` | Índice de ADRs | Navegación |
| `docs/mce-evolution.md` | Trabajo estructural diferido con triggers | Al arrancar cada fase |
| `docs/checklist-value-investing.md` | Checklist canónico en detalle | Al tocar agentes de análisis o prompt del Value Expert |
| `memory/` (user-level) | Preferencias cross-project del usuario | Automático vía sistema de auto-memoria |

---

## Diagramas

Cualquier diagrama en documentación (README, CLAUDE.md, ADRs, DETAILED_EXPLANATION.md) debe usar **Mermaid**, no ASCII art. GitHub renderiza Mermaid nativamente. Usar bloques ` ```mermaid `.

---

## Documentación del código (obligatorio en este proyecto)

Todo código generado debe incluir:

1. **Docstring de archivo** — primera línea después de los imports, explica qué hace el módulo, por qué existe y cómo encaja en la arquitectura. 3-5 líneas.
2. **Docstrings de funciones y clases** — en toda función o clase no trivial. Conciso: qué hace, qué recibe, qué devuelve, qué efecto tiene.
3. **Comentarios inline didácticos** — cuando el WHY no es obvio: decisiones de diseño, workarounds, invariantes que deben preservarse, conexiones con ADRs.

El objetivo es que Eduardo pueda leer cualquier archivo y entender qué hace sin necesidad de contexto externo.

**Nota**: esta regla tiene prioridad sobre el principio global "default to no comments". Eduardo está aprendiendo el ecosistema IA/agentic — los comentarios son parte del aprendizaje.

---

## Principios de diseño de código (recordatorio denso)

- **YAGNI ruthless**: rechazar cualquier componente no justificado por un requisito actual.
- **Pydantic schemas desde día 0**: todos los outputs de agente, tool y synthesis son Pydantic v2. No `dict` libre.
- **Async/await** donde tenga sentido (LangGraph lo soporta). No mezclar paradigmas gratuitamente.
- **Interfaces polimórficas** para capas que crecerán: `FilingsProvider`, `PricesProvider`, `TranscriptsProvider`. Facilita Fase 3 (CAD/EU).
- **Config por env vars** (`.env` + `pydantic-settings`). Zero secretos en código. Paths relativos al root del proyecto.
- **Tests con fixtures cacheados** — un análisis reproducible sin internet. Raw API responses se guardan en `tests/fixtures/`.

---

## Comandos de proyecto (se actualizará cuando existan)

```bash
# Setup
uv sync

# Run smoke test
uv run pytest tests/test_smoke.py -q

# Run full analysis (CLI — cuando esté)
uv run due-diligence analyze AAPL
```

---

## Modo Profesor (enseñanza orgánica) — REGLA DE HARD STOP

### Antes de cada tarea o componente nuevo
1. Explicar en texto (NO código): qué es, por qué existe, cómo encaja en la arquitectura.
2. **PARAR. Preguntar explícitamente: "¿Tienes dudas antes de que empiece?"**
3. Solo continuar cuando Eduardo confirme o no tenga preguntas.

### Durante la implementación
- Máximo 1 tarea por turno sin checkpoint intermedio.
- Si una tarea tiene >3 pasos, pausar a mitad y resumir qué se ha hecho y qué falta.
- Nunca encadenar 2+ tareas en el mismo turno sin confirmación explícita de Eduardo.

### Después de cada tarea o milestone
- Escribir un resumen corto: qué se construyó, qué problema resuelve, qué aprendizaje clave hay.
- Actualizar `DETAILED_EXPLANATION.md` (root del proyecto) con ese resumen en formato didáctico.
- **PARAR. Preguntar: "¿Quieres que explique algo más antes de la siguiente tarea?"**

### DETAILED_EXPLANATION.md
- Vive en la raíz del proyecto.
- Acumula explicaciones didácticas de todo lo construido, ordenado por fase y tarea.
- Formato: concepto → por qué existe → cómo funciona → relación con la arquitectura → ejemplo concreto del código.
- Se actualiza al finalizar cada tarea, fase o milestone significativo.

### Anti-patrón a evitar (lo que NO debe pasar)
Implementar todas las tareas de una fase de forma encadenada sin ningún checkpoint, sin explicaciones previas, sin pausas. Esto rompe el objetivo dual del sistema (decidir + aprender).

### Excepciones
- Pasos mecánicos dentro de una tarea ya explicada (p.ej. `git commit` o `uv sync`): no requieren pausa.
- Arreglos obvios de un error de sintaxis en código recién explicado: no requieren pausa.

---

## Qué NO hacer en este proyecto

- No hacer commits, amends, pushes ni PRs sin orden explícita (regla global).
- No tocar `.env` ni memorizarlo en contexto (contiene secretos).
- No cachear respuestas API con datos sensibles sin cifrar.
- No añadir "workarounds temporales" — siempre root cause. Si no hay tiempo, abrir issue.
- No presentar métricas generadas por LLM como si vinieran del filing (ADR-005).
- No omitir la sección VERDICT del Value Expert cuando se reformatee el report (ADR-009).
