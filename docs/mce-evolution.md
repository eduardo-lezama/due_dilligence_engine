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

### MCE-19 — News coverage extendida (Tavily + ventana adaptativa) — RESUELTO 2026-05-10
Implementado en commit pendiente. Tavily se invoca complementariamente cuando el stock está en drawdown (`price_current / price_52w_high < 0.70`). 3 queries targeting catalizadores típicos: profit warnings/guidance miss, tariff/supply chain, regulatory risk. Sin filtro temporal estricto (`time_range=None`). Configurable via `MARKET_NEWS_INCLUDE_TAVILY` y `MARKET_NEWS_DRAWDOWN_THRESHOLD`. 2 tests añadidos.

### MCE-20 — 8-K filter ampliado y configurable — RESUELTO 2026-05-10
Implementado en commit pendiente. Default expandido a 6 items (`1.01,2.02,4.02,5.02,7.01,8.01`). Configurable via `EDGAR_8K_ITEMS` env var como lista CSV.

### MCE-21 — Reverse DCF como headline cuando hay contradicción — RESUELTO 2026-05-10
Implementado en commit pendiente. Archiver detecta `dcf_reliability_note` con "contradiction" → reordena la sección Valuation: warning sube a la cabecera, fila destacada "Implied growth (reverse DCF — primary anchor)" como primaria, forward DCF marcado como "*conditional on growth assumption*". Reading guide explícito antes de la tabla.

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

### MCE-24 — Quarterly margin trends desde 10-Q (margin compression QoQ) — **PARCIALMENTE RESUELTO 2026-05-15**
- **Status**: Light implementation completada (ADR-025). Full margin trend analysis deferred to Phase 3+.
- **Implementado**: `EdgarAdapter.fetch_quarterly_snapshot()` extrae revenue, operating_income, gross_profit del 10-Q más reciente cuando 10-K >90 días stale. Surfaceado en Value Expert como "QUARTERLY XBRL" section.
- **Deferred**: QoQ margin trend analysis (4 quarters, compression detection). Razón: 80% del valor con 20% del esfuerzo — snapshot de 1 quarter cubre casos críticos (MRCY-like).
- **Trigger para full implementation**: cuando se detecten casos donde el snapshot de 1 quarter no es suficiente (necesidad de tendencia multi-quarter).
- **Estimación remaining**: ~4-6h (extender a 4 quarters, calcular deltas QoQ, flag compression)
- **Prioridad**: baja — nice-to-have, no blocker

### MCE-25 — Test fixtures multi-arquetipo (large cap + small cap + buyback-heavy + drawdown) — RESUELTO 2026-05-16
- **Arquetipos implementados**: AAPL (baseline), BBW (small cap), AZO (buyback-heavy equity negativo), PRDO (asset-light capex ausente).
- **Fixtures**: `tests/fixtures/{aapl,bbw,azo,prdo}_xbrl_raw.json`
- **Tests**: `tests/test_edgar_archetypes.py` — 5 critical-path tests × 4 arquetipos = 20 tests.
  - Smoke (parse_financials no crash), revenue not empty, FCF no $0.0B (EC-14), IC never None (EC-18), ROIC graceful (EC-21).
- **Fixture fetch**: `scripts/fetch_archetype_fixtures.py` — script one-shot para añadir arquetipos nuevos.
- **Para añadir un arquetipo futuro**: añadir ticker a `ARCHETYPES` en el script + a `_ARCHETYPE_TICKERS` en `conftest.py`. Los 5 tests corren automáticamente.
- **Commits**: `47f4688` (fixtures + script), siguiente commit (conftest + tests).

### MCE-26 — Auditoría sistemática post-fix — RESUELTO 2026-05-10
Sección "Cuando se arregla un bug — auditoría sistemática post-fix" añadida al `CLAUDE.md` raíz. Regla obligatoria de 4 pasos: identificar patrón → grep `src/` completo → arreglar/documentar cada ocurrencia → declarar fix completo. Previene regresiones tipo EC-14→EC-17.

### MCE-27 — Soporte de empresas financieras (SIC 6000-6799) — Phase 3+
- **Trigger**: cuando Eduardo quiera analizar bancos, insurers, mortgage cos, brokers o REITs como parte de su universe de value investing (Buffett invierte en bancos — es parte del juego).
- **Problema detectado** (EC-35): el framework actual (DCF, EV/EBIT, ROIC, capex, gross margin) NO aplica a empresas financieras. Hoy se rechazan con detect-and-fail-loud. Pero financials SÍ son universe value relevante.
- **Trabajo requerido**:
  1. **Detector mejorado**: SIC + concept-based detection (presencia de `InterestIncomeOperating`, ausencia de `Revenue`). Hoy solo SIC.
  2. **Schema financiero** (`FinancialsReportFinancial`): NetInterestIncome, NetInterestMargin, Tier1Capital, NPL ratio, allowance for loan losses, book value per share, tangible book value.
  3. **Agente de valoración financiera**: P/TBV multiple, P/E normalizado, dividend discount model, ROE vs cost of equity.
  4. **Agentes adaptados**: management agent (proxy statement diferente), competitive (peer set distinto), value_expert prompt (checklist financial).
  5. **Sub-clasificación por SIC**:
     - 6000-6099 (banks): NII, NIM, deposit franchise, credit quality
     - 6100-6199 (non-depository credit): loan portfolio quality, funding cost
     - 6300-6499 (insurance): combined ratio, reserves, float
     - 6500-6599 + 6700-6798 (REITs): FFO, AFFO, NAV, occupancy, lease structure
- **Estimación**: ~2-3 semanas (es un framework paralelo, no un parche)
- **Prioridad**: media — depende de cuánto del portfolio personal de Eduardo sean financials. Hoy rechazo loud educa al usuario.

### MCE-28 — Cross-validator pre-Expert (data integrity layer)
- **Trigger original**: EC-37 (audit MOS 2026-05-13) demostró que un dato XBRL roto (Debt $0 cuando real $5B) llega al Critic igual de roto que al Expert. El Critic actual razona sobre los mismos datos que el Expert — no puede detectar errores de fuente.
- **Decisión actual (2026-05-15)**: **NO implementar ahora**. El "external audit pattern" (Eduardo audita reports con Opus 4.7 en sesión paralela) sigue siendo el mecanismo de validación cross-source preferido. Razones:
  - EC-37 ya está arreglado en la fuente (raw facts fallback) — el bug catastrófico que motivó el análisis no puede recurrir igual
  - El external audit con Opus es flexible, gratuito (Claude Pro), y captura más que data integrity — también catches reasoning gaps y prompt issues
  - Internalizar la validación añade complejidad arquitectónica (otro nodo, fuente externa de ground truth, threshold tuning) sin garantía de coverage superior
  - El patrón "humano + LLM external" tiene mejor signal-to-noise que un validator programático ciego
- **Trigger para reactivar**: si aparece otro caso EC-37-class donde un dato crítico del XBRL diverge silenciosamente de la realidad Y el external audit pattern no lo cazó. Hasta entonces, mantener external audit como mecanismo primario.
- **Propuesta diseño** (cuando se active):
  1. **Nodo nuevo `validator_node`** entre `financials_node` y el fan-out de especialistas (ANTES del Expert, no después como un Critic con tools)
  2. **Tool-lane puro** — sin LLM, código deterministic con fuente externa
  3. **Checks de integridad**:
     - **Balance sheet identity**: `Assets ≈ Equity + Liabilities` (chequeo intrínseco, no requiere fuente externa)
     - **Cross-source debt comparison**: nuestro `total_debt` vs Yahoo Finance / StockAnalysis (vía Tavily targeted query o API directa si disponible)
     - **Market cap reconciliation**: nuestro `market_cap` (Polygon) vs Yahoo
     - **Recent quarterly check**: si hay 10-Q post-10-K, revenue/net income del último Q vs anualizado
  4. **Output**: lista de discrepancies con magnitude y direction, escrita a `state["data_validation_warnings"]`
  5. **Consumers**: tanto Expert (§4 Financial Quality) como Critic reciben los warnings en context — el Expert puede ajustar conclusión, el Critic puede usar como pivote
  6. **No bloquea**: solo informa. La decisión de continuar/abortar se delega al Expert
- **Por qué Opción D y no tool calls en Critic (Opción C)**:
  - Separación de concerns: Critic = red-team reasoning. Validator = data verification. Mezclar las debilita ambas
  - Catch EARLIER: si Debt está mal, todo el reasoning downstream es mentira. Validador en pre-Expert evita el reasoning falso desde el principio
  - Preserva el patrón tool-lane / LLM-lane del proyecto (ADR-005). Tool calls en Critic introduce reasoning sobre tool outputs que no podemos validar
  - Determinismo y testability: validator es código puro con fixtures. Critic con tools introduce non-determinism por search results variables
- **Estimación**: ~6-8h (nodo + fuente externa + tests con fixtures de discrepancia)
- **Coste runtime**: ~$0.005 + 2-3s latency por análisis (Tavily targeted queries — barato comparado con el coste del análisis completo)
- **Prioridad**: media-baja mientras el external audit pattern funcione. Reactivar si:
  - Eduardo no quiere seguir haciendo audits manuales con Opus
  - Aparece un EC-37-class bug que el external audit no caza
  - El proyecto pasa a Phase 5 (webapp multi-usuario) donde el external audit no escala
- **Referencia conversación**: análisis arquitectónico completo en sesión 2026-05-14 (`dd-engine`).

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
