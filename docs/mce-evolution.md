# MCE Evolution — Deferred Structural Work

Lista viva de trabajo estructural (subdirectorios `CLAUDE.md`, skills, slash-commands, hooks, features) que **hoy decidimos NO crear** pero cuyo trigger sí tenemos claro. Cuando el trigger se cumpla, se ejecuta la acción.

**Única fuente de verdad** de trabajo diferido de MCE. Root `CLAUDE.md` instruye a revisar este archivo al arrancar fases y crear directorios significativos.

---

## Cómo usar este archivo

1. **Antes de arrancar una fase del roadmap** (ver `README.md` §6), escanear esta lista.
2. **Al crear un directorio significativo nuevo** (p.ej. `src/due_diligence/agents/`), escanear esta lista.
3. Para cada ítem con trigger cumplido: ejecutar la acción, mover el ítem a la sección "Resueltos" con fecha.
4. Si detectas trabajo diferido nuevo durante el desarrollo, añadir un ítem nuevo **con trigger explícito**.

**Regla**: no trigger → no ítem aquí. Trabajo futuro abstracto va al backlog del README, no aquí.

---

## Deferred items (activos)

### MCE-01 — Subdirectorio `src/due_diligence/CLAUDE.md`
- **Trigger**: creación de `src/due_diligence/` (Fase 0)
- **Acción**: CLAUDE.md con convenciones de código del módulo — Pydantic v2 patterns, imports, async/await, manejo de errores, logging estructurado, naming.
- **Estimación**: 80-120 líneas
- **Por qué diferido**: no existe el directorio aún
- **Prioridad**: alta (primera cosa al scaffoldear)

### MCE-02 — Subdirectorio `src/due_diligence/tools/CLAUDE.md`
- **Trigger**: creación de `src/due_diligence/tools/` (Fase 0-1)
- **Acción**: convenciones para data-source adapters — interface polimórfica `FilingsProvider` / `PricesProvider` / `TranscriptsProvider`, pattern de caching, rate-limit handling, retries con backoff, fail-loud policy (ADR-005).
- **Estimación**: 60-100 líneas
- **Prioridad**: alta

### MCE-03 — Subdirectorio `src/due_diligence/agents/CLAUDE.md` — **TRIGGER CUMPLIDO (Phase 1a)**
- **Trigger**: creación de `src/due_diligence/agents/` (Fase 1) ✓
- **Acción**: cómo implementar un especialista — Pydantic schema del output, node function pattern, fan-out vía `Send()`, testing con fixtures, citations mandatory (ADR-005).
- **Estimación**: 80-120 líneas
- **Prioridad**: alta

### MCE-04 — Subdirectorio `src/due_diligence/llm/CLAUDE.md`
- **Trigger**: creación de `src/due_diligence/llm/` (Fase 0)
- **Acción**: reglas del provider wrapper — nunca importar `anthropic`/`openai` fuera de aquí, prompt caching strategy, retry + fallback logic, cost tracking, model routing por rol (ADR-004).
- **Estimación**: 50-80 líneas
- **Prioridad**: alta

### MCE-05 — Subdirectorio `tests/CLAUDE.md`
- **Trigger**: creación de `tests/` (Fase 0)
- **Acción**: fixture-first testing (zero API calls reales), pattern de golden fixtures para filings cacheados, naming (`test_<unit>.py`), uso de pytest fixtures vs factory-boy, coverage expectations.
- **Estimación**: 50-80 líneas
- **Prioridad**: alta

### MCE-06 — Hook `PreToolUse` para bloquear lectura de secretos
- **Trigger**: existencia de `.env` con valores reales en el proyecto
- **Acción**: configurar hook en `.claude/settings.json` que bloquee lecturas de `.env`, `**/secrets/**`, `**/*.pem`, `**/*.key`, `tokens.json`.
- **Estimación**: ~30 líneas de config + script si hace falta
- **Prioridad**: media (seguridad)

### MCE-07 — Slash command `/validate`
- **Trigger**: existencia simultánea de suite de tests + config de lint (`ruff.toml`) + typecheck (`mypy.ini` o equivalente)
- **Acción**: crear `.claude/commands/validate.md` que ejecute `uv run pytest -q && uv run ruff check && uv run mypy src/` con output agregado.
- **Estimación**: ~40 líneas
- **Prioridad**: media

### MCE-08 — Slash command `/new-adr`
- **Trigger**: cuando se necesite añadir un ADR por segunda vez tras el batch inicial (Fase 0-1)
- **Acción**: `.claude/commands/new-adr.md` que crea `docs/adr/ADR-NNN-<slug>.md` con template estándar, detecta el siguiente N libre, y recuerda actualizar el índice.
- **Estimación**: ~50 líneas
- **Prioridad**: baja (útil, no crítico)

### MCE-09 — Skill `new-specialist-agent`
- **Trigger**: 2º especialista implementado y se detecta patrón repetitivo
- **Acción**: `.claude/skills/new-specialist-agent/SKILL.md` con template — boilerplate del Pydantic schema, node function signature, Send() wiring, test fixtures, citation validation hookup. Incluir archivos de soporte (template `.py.jinja` si aplica).
- **Estimación**: ~100 líneas + template
- **Prioridad**: media (cuando aparezca el patrón)

### MCE-10 — Skill `new-data-source-adapter`
- **Trigger**: 2º adapter de data source implementado (p.ej. al sumar Polygon tras EDGAR)
- **Acción**: skill con template de adapter polimórfico — interface compliance, caching layer, rate-limit strategy, retry/backoff, testing con response fixtures.
- **Estimación**: ~100 líneas + template
- **Prioridad**: media

### MCE-11 — Skill `add-adr`
- **Trigger**: Cuando `/new-adr` slash-command no alcance y se necesite lógica más compleja (p.ej. cross-link con README, validación de numeración, superseding workflow)
- **Acción**: skill más rico que slash-command, con procedimiento paso a paso.
- **Estimación**: ~80 líneas
- **Prioridad**: baja

### MCE-12 — Vector RAG de memoria (Phase 2.5)
- **Trigger**: **≥3 análisis del mismo ticker** en SQLite (trigger corregido — ver ADR-013, que supersede ADR-008)
- **Rationale**: el caso de uso real es delta entre re-análisis del mismo ticker, no comparación cross-ticker (que ya cubre el peer comparison user-supplied de ADR-015).
- **Acción**: ChromaDB local, embeddings `text-embedding-3-small`, corpus = "analysis fingerprint" (VERDICT + moat + DCF assumptions + critic weaknesses), tool `retrieve_similar_analyses` expuesto al Value Expert. Ablation test con/sin RAG.
- **Estimación**: módulo completo (no solo MCE)
- **Prioridad**: alta cuando se tengan ≥3 análisis del mismo ticker

### MCE-13 — Adapters CAD + EU
- **Trigger**: arranque Fase 3 (analyst US maduro, portfolio con interés canadiense o europeo)
- **Acción**: implementar `SedarProvider` (CAD) y `EsmaProvider` / `CnmvProvider` / `FcaProvider` (EU). Multi-language handling en filings. Multi-currency normalization.
- **Prioridad**: alta cuando Fase 3 arranque

### MCE-14 — Docker + Container Station deploy
- **Trigger**: arranque Fase 5 (webapp en NAS)
- **Acción**: `Dockerfile`, `docker-compose.yml`, secrets management, volume mounts para storage persistente en NAS, docs de setup en Container Station, auth ligera.
- **Prioridad**: alta cuando Fase 5 arranque

### MCE-15 — Slash command `/mce-check`
- **Trigger**: cuando la lista crezca a ≥15 ítems — **TRIGGER CUMPLIDO (16 ítems con MCE-16)**
- **Acción**: `.claude/commands/mce-check.md` que lee este archivo, para cada ítem verifica si su trigger se cumple (bash checks — `test -d`, `test -f`, conteo en SQLite, etc.) y reporta tabla de ítems triggered-y-pendientes.
- **Prioridad**: baja (útil pero hoy cabe en la cabeza)

### MCE-17 — Pre-fetch 10-K antes del fan-out (primary source para competitive)
- **Trigger**: arranque Phase 2.5 o Phase 3
- **Problema detectado**: `competitive_agent` cita Yahoo Finance para hechos que están en el 10-K (backlog, capacidad contratada, volúmenes) porque corre en paralelo con `filings_agent` y no ve su output. El fan-out distribuye el state ANTES de que cualquier especialista escriba en él.
- **Solución**: nuevo nodo `edgar_prefetch_node` entre `financials_node` y el fan-out. Fetcha Item 1, 1A y 7 del 10-K y los escribe en `state["filings_raw_text"]`. Así todos los especialistas del fan-out tienen acceso a los textos primarios sin duplicar llamadas a EDGAR.
- **Acción concreta**:
  1. `supervisor_node` o nuevo `edgar_prefetch_node` fetcha `EdgarManagementAdapter.fetch_10k_business_text(ticker)` + `fetch_10k_risk_factors(ticker)` + `fetch_10k_mda(ticker)` y los escribe en state.
  2. `competitive_agent` lee `state["filings_raw_text"]` como SOURCE adicional y cita desde ahí para hechos de backlog/contratos/capacidad.
  3. `filings_agent` puede reusar el texto ya cacheado en state en lugar de re-fetchear.
- **Alternativa más simple** (si la refactorización es excesiva): añadir al prompt de competitive: "Para cifras de backlog, contratos o capacidad instalada: si la fuente es web, indica '(fuente: web — pendiente verificar en 10-K Item 7)'."
- **Prioridad**: media — mitiga una violación parcial de ADR-005 (datos cualitativos de backlog desde fuente secundaria)

### MCE-18 — Peer comparison enriquecida: múltiplos de valoración + Magic Formula
- **Trigger**: Phase 2.5 o cuando el usuario quiera relative valuation sectorial
- **Problema actual**: los peers aportan solo ROIC (1 dato). Para relative valuation se necesita también EV/EBIT de peers, P/E de peers, revenue growth, FCF yield — para ver si la empresa analizada está cara/barata vs su sector.
- **Datos ya disponibles sin nueva API**: `FinancialsReport` de peers ya tiene revenue, EBIT, FCF. Solo falta el precio de mercado de cada peer (Polygon API) para calcular EV.
- **Acción concreta**:
  1. `peers_node`: para cada peer, llamar a `PolygonAdapter.fetch_market_data(peer_ticker)` además de EDGAR (precio + market cap).
  2. `peers.py` o nuevo `PeerValuation` Pydantic model: `ticker, roic, ev_ebit, pe, fcf_yield, revenue_cagr_3y`.
  3. Archiver: tabla de relative valuation con sujeto como primera fila.
  4. Value Expert: los múltiplos de peers viajan a §6 Valuation para contextualizar si el precio actual está caro/barato vs sector.
  5. Magic Formula rank: rankear la empresa analizada en earnings yield + ROIC vs peers — el `ValuationReport.magic_formula_rank` lleva pendiente desde Phase 1.
- **Estimación**: ~3-4h (peers_node + schemas + archiver + context)
- **Prioridad**: media-alta para análisis sectoriales, baja si el portfolio es de empresas sin peers obvios

### MCE-19 — News coverage extendida (Tavily complement + ventana adaptativa) [Oleada 2.1]
- **Trigger**: cuando un análisis de empresa en drawdown (>30% desde 52w high) omita la noticia clave en el report (caso BBW: tariff overhang de Dec 2025 invisible al sistema)
- **Problema detectado**: `market_agent` solo usa Finnhub con ventana fija de 30 días y máximo 10 artículos. Para una small cap con drawdown del -48%, el catalizador suele ser una noticia de hace 3-6 meses (guidance miss, profit warning, regulatory event). Finnhub puede no devolverla en top 10.
- **Solución propuesta**:
  1. Añadir Tavily search en `market_agent` con queries: `"{company_name} stock decline guidance risk concern {year}"`, `"{ticker} earnings tariff regulatory risk"`. Usar `time_range=None` para histórico amplio + `time_range="month"` para reciente.
  2. Ventana adaptativa configurable: si `price_current / price_52w_high < 0.7`, expandir lookback a 180 días. Si stock está near-high, mantener 30 días.
  3. Variables `.env`: `MARKET_NEWS_DAYS_DEFAULT` (30), `MARKET_NEWS_DAYS_DRAWDOWN` (180), `MARKET_NEWS_INCLUDE_TAVILY` (true).
  4. Schema: `MarketReport.news_articles: list[NewsArticle]` (estructurado, no solo summary string) para que el Value Expert pueda filtrar.
- **Estimación**: ~3-4h
- **Prioridad**: alta para análisis de stocks en drawdown, donde mayor valor ofrece el sistema

### MCE-20 — 8-K filter ampliado y configurable [Oleada 2.2]
- **Trigger**: análisis de stock con drawdown que muestre "No material 8-K events" cuando claramente debería haberlos (BBW caso real)
- **Problema detectado**: `EdgarEventsAdapter.fetch_8k_events` filtra solo `[2.02, 4.02, 5.02]`. Faltan items relevantes para tracking de eventos:
  - **Item 1.01** Material Definitive Agreement (M&A, contratos relevantes)
  - **Item 7.01** Reg FD Disclosure (guidance updates fuera de earnings)
  - **Item 8.01** Other Events (anuncios materiales — incluye disclosures de tariffs, supply chain, lawsuits)
- **Solución propuesta**:
  1. Default expandido a `[1.01, 2.02, 4.02, 5.02, 7.01, 8.01]`.
  2. Configurable via `EDGAR_8K_ITEMS` en `.env` (lista separada por comas).
  3. Cada item con etiqueta legible en archiver ("Item 7.01 — Reg FD Disclosure").
- **Estimación**: ~1h
- **Prioridad**: media-alta — fix simple y bajo riesgo

### MCE-21 — Reverse DCF como número primario cuando hay contradicción [Oleada 2.3]
- **Trigger**: cuando el `dcf_reliability_note` contiene flag de contradicción
- **Problema detectado**: hoy el flag es texto en blockquote después de la tabla. El forward DCF sigue siendo el "número headline" en la tabla de Aggregate Metrics aunque hayamos declarado que es engañoso.
- **Solución propuesta**: archiver detecta flag de contradicción → reordena la tabla. Fila destacada "Market-implied value (reverse DCF anchor)" como primaria, forward DCF baja a fila secundaria con disclaimer "(conditional on growth assumption)".
- **Estimación**: ~1h
- **Prioridad**: media — impacto cosmético-pero-importante para quien lee el report rápido

### MCE-22 — Reglas de reconciliación de señales en Value Expert prompt — RESUELTO 2026-05-10
Implementado en commit pendiente. Tres reglas duras añadidas al `_SYSTEM_PROMPT`:
- §3 Management: reconcile insider sale prices vs current market price (gap >15%).
- §5 Growth: report reinvestment rate (precomputado en `_format_context` como capex/OCF con etiqueta invest/balanced/harvest mode).
- §7 Risks: cross-check news + insider + margin trends → "value trap signal pattern".
4 tests añadidos en `test_value_expert.py`.

### MCE-23 — Critic recibe contexto explícito de contradicciones detectadas — RESUELTO 2026-05-10
Implementado en commit pendiente. Nueva función `_detect_red_flags(state)` en `critic.py` que devuelve flags estructurados:
- DCF forward/reverse contradiction (desde `valuation.dcf_reliability_note`).
- Capital returns >80% del FCF (mature business, no reinvestment).
- Margin compression 3+ años consecutivos.
- Price collapse + insider activity (ratio current/52w_high < 0.6).

Sección `## DETECTED FLAGS` añadida al `_build_critic_prompt`. 5 tests en `test_critic.py`.

### MCE-24 — Quarterly margin trends desde 10-Q (margin compression QoQ)
- **Trigger**: Phase 2.5 o cuando el feedback consistente sea que los reports detectan margin compression solo a nivel anual
- **Problema detectado**: 10-K es anual. Para una empresa cuyo Q3-Q4 muestra deterioro pero el FY total todavía no lo refleja, el sistema dice "margin improving" cuando la realidad reciente es "margin deteriorating".
- **Solución propuesta**: parsear los últimos 4 trimestres de 10-Q para gross/operating margin. Comparar último Q vs Q anterior. Si compresión QoQ ≥ 100bps en 2+ trimestres consecutivos, flag.
- **Estimación**: ~6-8h (nuevo adapter, schema extension, integration en value_expert)
- **Prioridad**: media — feature mayor, no fix

### MCE-25 — Test fixtures multi-arquetipo (large cap + small cap + buyback-heavy + drawdown)
- **Trigger**: cuando se introduzca un fix más cuyo origen sea "diseño centrado en AAPL ideal"
- **Problema detectado**: pattern recurrente — el sistema se diseñó/testeó contra AAPL-like (large cap, equity grande, news flow constante). Falla en small caps, buyback-heavy, drawdown stocks. Ya hemos visto esto en EC-14, EC-15, EC-16, EC-18, EC-19, EC-20.
- **Solución propuesta**: añadir fixtures cacheadas a `tests/fixtures/` para 4 arquetipos:
  - **Large cap normal**: AAPL (ya existe)
  - **Small cap rentable**: BBW
  - **Buyback-heavy con equity bajo/negativo**: AZO o DPZ (test de ROIC fallback)
  - **Stock en drawdown**: cualquier ticker con price_52w_low / price_52w_high < 0.5
  - **Asset-light services**: PRDO o similar (test de zero capex)
- **Acción**: cada nuevo bug debe validarse contra los 4 arquetipos antes de declarar el fix completo. Esto convierte la búsqueda manual de bugs duplicados (que falló en EC-17) en regression test estructurado.
- **Estimación**: ~4-6h (fetchear fixtures + reescribir tests críticos para parametrizarse sobre arquetipos)
- **Prioridad**: alta — meta-pattern fix, previene categorías enteras de regresiones

### MCE-26 — Auditoría sistemática post-fix — RESUELTO 2026-05-10
Sección "Cuando se arregla un bug — auditoría sistemática post-fix" añadida al `CLAUDE.md` raíz. Regla obligatoria de 4 pasos: identificar patrón → grep `src/` completo → arreglar/documentar cada ocurrencia → declarar fix completo. Previene regresiones tipo EC-14→EC-17.

### MCE-16 — Skill `modo-profesor` (Socratic teaching patterns)
- **Trigger**: ≥3 sesiones reales de desarrollo con la regla "Modo Profesor" de CLAUDE.md activa y patrones claros de qué funciona (analogías, secuenciación de conceptos, preguntas tipo)
- **Acción**: `.claude/skills/modo-profesor/SKILL.md` con patrones Socráticos cristalizados — biblioteca de analogías para conceptos del stack (LangGraph, Pydantic, XBRL), secuencias de introducción de conceptos por capa, preguntas tipo para verificar comprensión, reglas de cuándo pausar vs avanzar.
- **Estimación**: ~100 líneas
- **Por qué diferido**: hoy no tenemos datos de qué patrones son útiles; la regla en CLAUDE.md cubre la necesidad actual
- **Prioridad**: media

---

## Resueltos

### MCE-01 — Subdirectorio `src/due_diligence/CLAUDE.md` — RESUELTO 2026-04-19
Creado en Phase 0 scaffolding. Contiene convenciones de código del módulo (naming, async, structlog, Pydantic invariants). Vive en `src/due_diligence/CLAUDE.md`.

### MCE-02 — Subdirectorio `src/due_diligence/tools/CLAUDE.md` — RESUELTO 2026-04-19
Creado en Phase 0 scaffolding. Contiene convenciones para data-source adapters (fetch_raw/parse split, fixture-first, fail-loud policy). Vive en `src/due_diligence/tools/CLAUDE.md`.

### MCE-04 — Subdirectorio `src/due_diligence/llm/CLAUDE.md` — RESUELTO 2026-04-19
Creado en Phase 0 scaffolding. Contiene reglas del provider wrapper (no imports directos de anthropic/openai, fallback, prompt caching). Vive en `src/due_diligence/llm/CLAUDE.md`.

### MCE-03 — Subdirectorio `src/due_diligence/agents/CLAUDE.md` — RESUELTO 2026-04-19
Creado en Phase 1a. Contiene convenciones para specialist agent nodes (función pura, return parcial, tool vs LLM lane, citas obligatorias, testing). Vive en `src/due_diligence/agents/CLAUDE.md`.

### MCE-05 — Subdirectorio `tests/CLAUDE.md` — RESUELTO 2026-04-19
Creado en Phase 0 scaffolding. Contiene convenciones de fixture-first testing (zero API calls, naming, pytest-mock, coverage mínima). Vive en `tests/CLAUDE.md`.

Cuando se resuelva un ítem, mover aquí con formato:
```
### MCE-XX — Título — RESUELTO 2026-MM-DD
Breve descripción de qué se hizo y dónde vive ahora.
```

---

## Cómo añadir un ítem nuevo

1. Elegir siguiente número disponible.
2. Definir **trigger explícito y verificable** (no "cuando sea útil" — p.ej. "cuando exista `src/X/`" o "cuando haya ≥N análisis").
3. Descripción concreta de la acción.
4. Estimación aproximada (líneas, módulos, horas).
5. Prioridad (alta/media/baja) relativa a las otras deferred items.
