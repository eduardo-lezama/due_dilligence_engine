# Due Diligence System

Sistema agentic personal de análisis fundamental de empresas con filosofía **value investing**. Multi-agente basado en LangGraph, provider-agnostic (Claude + OpenAI), con énfasis en **zero hallucination en datos numéricos** y modo **Socratic HITL** que combina análisis automatizado con aprendizaje continuo del usuario.

**Estado actual**: Phase 1 (1a + 1b + 1c) completa y verificada en producción.
**Owner**: Eduardo (uso personal, no comercial).
**Última actualización**: 2026-04-21.

```bash
uv run due-diligence analyze AAPL   # pipeline completo con HITL
```

---

## 1. Objetivo y principios

### 1.1 Objetivo
Construir un sistema que, dado un ticker US (luego CAD y EU), ejecute un **análisis fundamental exhaustivo al estilo value investing** apoyado en:
- Datos numéricos provenientes exclusivamente de fuentes primarias (SEC EDGAR XBRL, APIs autoritativas).
- Razonamiento cualitativo de LLMs frontera sobre filings, cartas anuales y earnings calls, con **citas verificables** a la fuente original.
- Intervención humana en puntos de alto valor (moat, management, supuestos DCF) con explicación didáctica.
- Archivo persistente de cada análisis para consulta futura y, en fases posteriores, retrieval-augmented de análisis previos.

### 1.2 Principios de diseño (no-negociables)
1. **Zero hallucination numérica**: los números no los genera el LLM, los extraen tools deterministas contra fuentes primarias.
2. **Citas obligatorias** en claims cualitativos: `{claim, source_url, excerpt, confidence}`, validadas post-hoc.
3. **Provider-agnostic**: Claude primario, OpenAI fallback. Sin lock-in de proveedor.
4. **Incremental + MVP-first**: cada fase entrega valor verificable antes de la siguiente.
5. **YAGNI ruthless**: rechazar cualquier componente que no resuelva un problema demostrado en la fase actual.
6. **Single-user, personal**: sin auth, sin multi-tenancy, sin SLAs.

### 1.3 Dual purpose (decisión del usuario)
El sistema sirve **dos objetivos simultáneamente**:
- Ayudar a decidir inversiones personales.
- Formar al usuario en análisis de valor (moats, management, skin in the game, calidad de earnings).
Esto justifica el patrón **Socratic HITL** — los checkpoints no son solo gates de aprobación, llevan razonamiento didáctico del sistema al usuario.

---

## 2. Decisiones al vistazo

| Área | Decisión | Referencia |
|---|---|---|
| Alcance Fase 1 | Analyst on-demand (screener diferido) | ADR-001 |
| Mercados | US → CAD → EU (en este orden) | ADR-002 |
| Arquitectura | Supervisor + especialistas paralelos (LangGraph) | ADR-003 |
| LLM stack | Claude primario + OpenAI fallback, multi-model por rol | ADR-004 |
| Control de alucinaciones | Tool-based extraction + schema+citas validadas | ADR-005 |
| HITL | Socratic checkpoints en 2 puntos (moat+mgmt, valoración) | ADR-006 |
| Storage Fase 1 | SQLite + markdown files | ADR-007 |
| RAG de memoria | Diferido a Fase 2 (corpus < 10 no justifica) | ADR-008 |
| Output del Value Expert | Análisis estructurado + veredicto explícito + sizing sugerido | ADR-009 |
| Deploy objetivo | NAS QNAP TS264 (webapp en fase 5 vía Docker) | ADR-010 |
| Fuentes de datos US | `edgartools` (XBRL), Polygon free, Finnhub | ADR-011 |

---

## 3. Architecture Decision Records

Las decisiones arquitectónicas viven como archivos individuales en [`docs/adr/`](docs/adr/). Formato estándar (Context · Decision · Consequences · Alternatives). Los ADRs son inmutables — cambiar una decisión requiere un ADR nuevo que supersede al anterior.

| # | Título | Status |
|---|---|---|
| [001](docs/adr/ADR-001-analyst-first.md) | Analyst-first, screener deferred | Accepted |
| [002](docs/adr/ADR-002-markets-order.md) | Mercados por orden US → CAD → EU | Accepted |
| [003](docs/adr/ADR-003-supervisor-parallel-specialists.md) | Supervisor + especialistas paralelos (Opción B) | Accepted |
| [004](docs/adr/ADR-004-provider-abstraction.md) | Provider abstraction Claude primary + OpenAI fallback | Accepted |
| [005](docs/adr/ADR-005-hallucination-control-by-architecture.md) | Control de alucinaciones por arquitectura, no por crítica | Accepted |
| [006](docs/adr/ADR-006-socratic-hitl.md) | Socratic HITL en 2 checkpoints | Accepted |
| [007](docs/adr/ADR-007-sqlite-markdown-storage.md) | SQLite + markdown para storage Fase 1 | Accepted |
| [008](docs/adr/ADR-008-vector-rag-deferred.md) | Vector RAG diferido a Fase 2 | Accepted |
| [009](docs/adr/ADR-009-verdict-plus-analysis.md) | Value Expert: análisis estructurado + veredicto | Accepted |
| [010](docs/adr/ADR-010-nas-deploy-target.md) | Deploy target NAS QNAP TS264 | Accepted |
| [011](docs/adr/ADR-011-us-data-sources.md) | Fuentes de datos US Fase 1 | Accepted |
| [012](docs/adr/ADR-012-management-agent-data-sources.md) | Fuentes de datos Management Agent (EDGAR trifecta + Tavily) | Accepted |

Ver [`docs/adr/README.md`](docs/adr/README.md) para la política de ADRs y cómo añadir uno nuevo.

---

## 4. Research findings (síntesis)

### 4.1 Fuentes de datos US (2026)
| Fuente | Tipo | Método | Reliability | Uso |
|---|---|---|---|---|
| SEC EDGAR XBRL vía `edgartools` | Filings + financials estructurados | Python lib local | **Primary** | Todas las cifras del 10-K/10-Q, comp, insider |
| SEC-API.io (free: 100/day) | Filings JSON + Form 4/13F | REST | Primary (proxied) | Form 4 convenience, backup |
| Polygon.io (free: 5 rpm) | OHLCV histórico | REST | Derived-clean | Precios, retornos, volatilidad |
| Alpha Vantage (free: 25/day) | Precios + splits | REST | Derived | Fallback de Polygon |
| Finnhub (free) | Transcripts, news | REST | Derived | Earnings calls transcripts |
| FinancialModelingPrep (free: 250/day) | Ratios pre-calculados | REST | Derived | Convenience en Fase 2 |
| OpenBB Platform | Aggregator | Python lib | Variable | Evaluar en Fase 3 |

**Gotchas a blindar**: XBRL tagging inconsistencies entre años (validación con Pydantic), restatements (10-K/A detectable vía 8-K Item 4.02), GAAP vs non-GAAP (flag obligatorio en cada metric), currency (siempre guardar `currency_code`).

### 4.2 Patrones de control de alucinaciones (state of the art 2025-2026)
1. **Tool-based numerical extraction**: LLMs tienen ~17% accuracy mapeando números a taxonomías GAAP. Solución: XBRL ya tagged → tool determinista → LLM solo razona. Ganancia demostrada: +42% en numeric reasoning.
2. **Schema-enforced outputs + citas validadas**: Pydantic fuerza shape, validación post-hoc hace substring-match contra filing. Caso documentado: reduce alucinación de 10% a 0%.
3. **Lane separation**: router semántico manda queries numéricas a tool-lane, queries cualitativas a RAG+LLM-lane. Nunca se mezclan.
4. **Verification loops**: eficacia limitada. Evitar self-critique costoso; invertir en validación post-hoc de citas.

Ejemplos de referencia (no forkear, aprender): FinRobot, FinGPT, BloombergGPT.

### 4.3 Checklist canónico value investing (mapeado a routing)
El analista ejecuta 9 secciones. Cada item está clasificado `quantitative` (tool lane), `qualitative` (LLM lane con citas) o `hybrid`.

1. **Business understanding** — two-minute story, revenue decomposition, circle of competence [qual + hybrid]
2. **Moat analysis** — tipo (Buffett/Munger), evidencia (margins, market share, pricing power), ROIC persistence, widening vs eroding [qual + quant]
3. **Management & skin in the game** — insider ownership %, Form 4 transactions, CEO tenure, compensation structure, capital allocation track record, shareholder letters [hybrid]
4. **Financial quality** — revenue CAGR, margins trend, FCF conversion, owner earnings, ROIC vs WACC, DuPont ROE, balance sheet, share count [quant]
5. **Growth & reinvestment** — incremental ROIC, reinvestment rate × incremental ROIC, TAM, unit economics [quant + hybrid]
6. **Valuation** — DCF + reverse DCF + multiples + PEG + earnings yield vs Treasury + Magic Formula + Graham Number where applicable [quant con assumptions hybrid]
7. **Risks & red flags** — customer/supplier concentration, regulatory, accounting red flags, debt walls, dilution, going concern, cycle position [hybrid]
8. **Psychological & contrarian lens** — mispricing thesis, variant view, base rates, pre-mortem [qual]
9. **Decision frame** — MOS target, position sizing, kill criteria, sell triggers, review cadence [hybrid]

(Full spec de cada ítem en [`docs/checklist-value-investing.md`](docs/checklist-value-investing.md).)

---

## 5. Arquitectura (Opción B)

### 5.1 Diagrama de alto nivel
```
                        ┌──────────────────────────┐
                        │  SUPERVISOR / PLANNER    │
                        │  (LangGraph entry node)  │
                        │  Model: Sonnet / gpt-4o  │
                        └──────────────┬───────────┘
                                       │ Send() fan-out
        ┌──────────────┬────────────┬──┴────────┬───────────────┬──────────────┐
        ▼              ▼            ▼           ▼               ▼              ▼
  ┌──────────┐  ┌────────────┐  ┌────────┐  ┌──────────┐  ┌────────────┐
  │Financials│  │ Mgmt &     │  │Filings │  │ Market & │  │Competitive │
  │ Agent    │  │ Skin Agent │  │RAG     │  │ News     │  │& Moat Agent│
  │          │  │            │  │Agent   │  │ Agent    │  │            │
  │ tool-lane│  │tool+LLM    │  │LLM+cits│  │ tool-lane│  │ LLM+cits   │
  │(edgar-   │  │(Form 4,    │  │(10-K   │  │(Polygon, │  │(10-K MD&A, │
  │ tools    │  │ DEF 14A,   │  │ MD&A,  │  │ Finnhub  │  │ peer cmp,  │
  │ XBRL)    │  │ letters)   │  │ calls) │  │ news)    │  │ moat tag)  │
  └────┬─────┘  └─────┬──────┘  └───┬────┘  └────┬─────┘  └─────┬──────┘
       │              │             │            │              │
       └──────────────┴─────────────┴────────────┴──────────────┘
                                  │ join into State
                                  │
                       ┌──────────▼──────────────┐
                       │ CHECKPOINT 1 (Socratic) │
                       │ "moat + mgmt review"    │  ← HITL interrupt()
                       └──────────┬──────────────┘
                                  │
                       ┌──────────▼──────────────┐
                       │   VALUATION AGENT       │
                       │  tool-lane:             │
                       │  DCF + reverse DCF +    │
                       │  multiples + Magic F.   │
                       │  Model: Sonnet          │
                       └──────────┬──────────────┘
                                  │
                       ┌──────────▼──────────────┐
                       │ CHECKPOINT 2 (Socratic) │
                       │ "valuation assumptions" │  ← HITL interrupt()
                       └──────────┬──────────────┘
                                  │
                       ┌──────────▼──────────────┐
                       │  VALUE EXPERT           │
                       │  (synthesis + verdict)  │
                       │  Model: Opus / gpt-4o   │
                       └──────────┬──────────────┘
                                  │
                       ┌──────────▼──────────────┐
                       │  CRITIC AGENT           │
                       │  (pre-mortem, diff model│
                       │   from Expert)          │
                       └──────────┬──────────────┘
                                  │
                       ┌──────────▼──────────────┐
                       │  ARCHIVER               │
                       │  write report.md + SQL  │
                       │  row + cache raw docs   │
                       └─────────────────────────┘
```

### 5.2 Componentes principales

**Supervisor / Planner**
- Recibe el ticker, hace una primera ingesta mínima (identifier validation, last filing date, sector classification).
- Decide qué especialistas son relevantes (un utility no necesita análisis de moat de marca).
- Asigna presupuesto de tokens por rama.
- Emite estado inicial para el fan-out.

**Especialistas (paralelos, en `Send()` de LangGraph)**
Cada especialista:
- Opera sobre una slice del State (`state.financials`, `state.mgmt`, etc.).
- Decide internamente si un sub-task va por tool-lane o LLM-lane.
- Emite outputs schema-validated (Pydantic).
- Adjunta citas con substring-match validado.

| Agente | Tool-lane | LLM-lane | Modelo actual |
|---|---|---|---|
| Financials | `edgartools` XBRL, ROIC calc | — | (sin LLM) |
| Mgmt & Skin | Form 4, DEF 14A, 10-K §10, Tavily | Síntesis cualitativa §3 checklist | `gpt-4.1` |
| Filings RAG | 10-K §1/1A/7 (edgartools) | Síntesis §1+§7 checklist | `gpt-4.1` |
| Market & News | Polygon (precios, MCap), Finnhub (noticias) | — | (sin LLM) |
| Competitive & Moat | ROIC series del estado, Tavily | Clasificación moat §2 checklist | `gpt-4.1` |

**Valuation Agent**
- Recibe inputs del usuario del Checkpoint 2 (WACC, growth, terminal).
- Ejecuta DCF, reverse DCF, multiples comp, Magic Formula rank — todo tool-based, zero LLM numeric generation.
- Output: rango de intrinsic values + sensitivity table.

**Value Expert (síntesis)**
- Modelo: `gpt-5.4-mini` (configurable via `OPENAI_EXPERT_MODEL` en `.env`).
- Inputs: todos los outputs de especialistas + Valuation + Checkpoint inputs.
- Output: análisis en 9 secciones + `verdict_rationale` (2-3 frases) + VERDICT BUY/HOLD/SELL + confidence + sizing sugerido (ver ADR-009).

**Critic Agent**
- Modelo: `gpt-5.4-mini` (mismo que Expert — diversidad viene del prompt opuesto).
- Ejecuta pre-mortem: "si este veredicto es erróneo en 3 años, ¿razón más probable?".
- Output: 3-5 bullets de weaknesses + contra-thesis + pre-mortem scenario.

**Archiver**
- Persiste report markdown, SQLite row, raw cache.
- Genera hash del análisis para deduplicación e idempotencia si se re-ejecuta.

### 5.3 State schema (boceto)
```python
class AnalysisState(TypedDict):
    ticker: str
    as_of_date: date
    # specialist outputs
    financials: FinancialsReport
    mgmt: ManagementReport
    filings: FilingsRAGReport
    market: MarketReport
    competitive: CompetitiveReport
    # HITL
    checkpoint1_user_input: Optional[dict]
    checkpoint2_user_input: Optional[dict]
    # downstream
    valuation: ValuationReport
    synthesis: ValueExpertReport
    critique: CriticReport
    # meta
    citations: list[Citation]          # all cited claims across agents
    unverified_claims: list[Claim]     # excluded from synthesis
    run_id: str
    cost_usd: float
```

### 5.4 Flujo de datos y control
- **Entrada**: `$ due-diligence analyze AAPL`
- **Supervisor** → valida ticker, fetch last filings metadata, decide routing.
- **Fan-out** (Send): 5 especialistas en paralelo. Cada uno escribe su slice al State.
- **Join** natural por LangGraph: el siguiente nodo espera a que todos terminen.
- **Interrupt 1** (Checkpoint 1): State serializado en checkpointer SQLite; usuario ve resumen didáctico en CLI; input reanuda.
- **Valuation** con inputs del checkpoint.
- **Interrupt 2** (Checkpoint 2): idem.
- **Value Expert** síntesis con prompt caching.
- **Critic** pre-mortem.
- **Archiver** persiste.
- **Exit**: CLI muestra report (con `rich`) y ruta del markdown generado.

### 5.5 Manejo de errores
- Tools determinísticos fallan loud (nunca fallback a LLM-number).
- LLM calls tienen retry con exponential backoff + switch a fallback provider tras 2 fallos.
- Claim con `confidence=0` → excluido de síntesis pero listado en `unverified_claims` del report (transparencia).
- Si un especialista falla completo, el Supervisor decide: continuar sin ese slice (degradación graceful con warning) o abortar (si es Financials o Valuation, abort).

---

## 6. Roadmap por fases

Cada fase entrega un **MVP funcional y verificable**. No se avanza a la siguiente hasta que la actual esté working end-to-end.

**Regla transversal (MCE gate)**: al **arrancar** cada fase, revisar [`docs/mce-evolution.md`](docs/mce-evolution.md) y ejecutar los ítems cuyo trigger se cumpla (subdirectorios CLAUDE.md, skills, slash-commands, hooks, etc.). Al **cerrar** la fase, añadir cualquier nuevo trabajo estructural diferido detectado durante la implementación.

### Fase 0 — Scaffolding (1 sesión)
**Entregable**: repo esqueleto + contratos + smoke test.
- Estructura del proyecto, `pyproject.toml` con `uv`, `.env.example`, `.gitignore`.
- `llm_client.py`: thin wrapper Claude + OpenAI con fallback básico.
- Pydantic models base: `Citation`, `Claim`, `MetricValue`, `FinancialsReport` (stub).
- Tool mínimo: `edgar_get_financials(ticker) -> FinancialsReport`.
- `tests/test_smoke.py`: ejecutar tool para AAPL y validar shape.
- `docs/checklist-value-investing.md`: el checklist §4.3 en detalle.
**Definition of done**: `pytest -q` verde; `python -m due_diligence.tools.edgar get-financials AAPL` imprime ratios correctos.

### Fase 1 — MVP Analyst US

Dividida en 3 milestones. No se avanza al siguiente hasta que el anterior esté end-to-end funcional.

#### ~~Fase 1a~~ ✓ COMPLETA (2026-04-20)
LangGraph skeleton + Supervisor + Financials specialist. `due-diligence analyze AAPL` extrae datos XBRL reales de EDGAR FY2025 end-to-end. ROIC calculado desde XBRL.

#### ~~Fase 1b~~ ✓ COMPLETA (2026-04-20)
4 especialistas en paralelo vía `Send()` fan-out + checkpoint1 HITL:
- `management_agent` — DEF14A + Form4 + 10-K §10 + Tavily → §3 checklist
- `filings_agent` — 10-K §1/§1A/§7 → §1+§7 checklist
- `market_agent` — Polygon (precio, MCap, 52w) + Finnhub (noticias), tool-lane
- `competitive_agent` — ROIC series + Tavily → §2 checklist (moat)

#### ~~Fase 1c~~ ✓ COMPLETA (2026-04-21)
Pipeline completo verificado en producción con AAPL y FSLR:
- `valuation_agent` — DCF + reverse DCF + multiples + sensitivity table 3×3, zero LLM
- `value_expert_agent` — 9 secciones + `verdict_rationale` + VERDICT, modelo `gpt-5.4-mini`
- `critic_agent` — red-team: weaknesses + contra-thesis + pre-mortem, modelo `gpt-5.4-mini`
- `archiver_node` — markdown con precio actual vs intrínseco por acción + SQLite row
- CLI interactivo con 2 checkpoints HITL: revisión de especialistas + assumptions DCF

**Limitaciones conocidas de Phase 1:**
- Solo empresas US con 10-K (foreign private issuers con 20-F: ASML, SAP, etc. no soportados hasta Phase 3)
- Checkpointer usa `MemorySaver` (no persiste entre sesiones — aceptable para run de ~3 min)
- `peer_roic_comparison` vacío hasta Phase 2 (requiere XBRL de peers)

### Fase 2 — RAG de memoria + news
**Precondition**: ≥10 análisis archivados (o umbral menor si quality lo justifica).
- ChromaDB local con embeddings `text-embedding-3-small`.
- Tool `retrieve_similar_analyses(profile)` expuesto al Value Expert.
- News ingestion (NewsAPI free o GDELT) al Market Agent.
- Archivado adicional: embedding del report + metadata indexable.
**Definition of done**: en un análisis nuevo, el Expert cita explícitamente ≥1 análisis previo comparable; ablation test (con vs sin RAG) muestra mejora cualitativa en la síntesis.

### Fase 3 — Multi-mercado CAD + EU
- Interface `FilingsProvider` formalizada.
- Adapter SEDAR+ (Canadá).
- Adapter ESMA / country-specific (empezar por UK FCA + España CNMV).
- Handling de multi-language filings (translate vía LLM con flag explícito).
- Handling de múltiples currencies con normalización explícita.
**Definition of done**: análisis funcional de 1 ticker canadiense (p.ej. Shopify o Constellation Software) y 1 europeo (p.ej. LVMH o Inditex) con mismo nivel de rigor que US.

### Fase 4 — Screener module
- Pipeline separado (no dentro del grafo del analyst).
- Filtros quantitativos: ROIC, earnings yield, ratios, momentum (opcional).
- Output: lista rankeada de candidatos que el usuario puede feed al analyst.
- Evaluar primero integración con FinViz / Koyfin free antes de construir inhouse.
**Definition of done**: weekly job produce 5-10 candidatos con rationale, user elige y pasa al analyst pipeline.

### Fase 5 — WebApp (NAS deployment)
- Backend FastAPI exponiendo los mismos graphs vía HTTP.
- Frontend Streamlit (rápido) o React ligero (más pulido, si hay energía).
- Dockerized stack corriendo en QNAP Container Station.
- Auth ligera (single-user, password básico para LAN).
- UI: iniciar análisis, ver checkpoints con UI decente, navegar histórico, búsqueda.
**Definition of done**: sistema accesible desde el móvil via LAN; análisis completos ejecutables desde UI; no regresiones vs CLI.

### Post-Fase 5 (backlog)
- Observability (Langfuse self-hosted en NAS).
- Alertas sobre posiciones en portfolio (portfolio tracking + trigger en métricas o news materiales).
- Exportación a PDF de reports.
- Integración con brokers (read-only, Plaid / IBKR API) para autopoblar portfolio.

---

## 7. Stack técnico (Fase 0-1)

| Capa | Tecnología | Razón |
|---|---|---|
| Runtime | Python 3.12 | Ecosistema agentic maduro |
| Package mgmt | `uv` | Rápido, ya usado en labs |
| Orquestación agentic | LangGraph | Ya conocido (labs 5, 7), interrupt + checkpointer nativos |
| LLM SDKs | `anthropic`, `openai` | Acceso directo, thin wrapper propio |
| Validación | Pydantic v2 | Schema-enforced outputs (ADR-005) |
| Filings US | `edgartools` | Primary source, zero rate limits |
| Precios | `polygon-api-client` | Free tier decente |
| Transcripts | `finnhub-python` | Free tier |
| CLI | `typer` + `rich` | UX presentable con poco código |
| Storage | SQLite + filesystem | YAGNI (ADR-007) |
| Testing | pytest + fixtures cacheados | Zero API calls en tests |
| Logging | `structlog` | Trazas estructuradas JSON |

**Explícitamente NO usados en Fase 0-1**: Redis, Celery, Postgres, LangChain (solo LangGraph), MCP servers, Docker, vector stores, observability SaaS (Langfuse/Arize).

---

## 8. Anti-overengineering (lo que NO hacemos y por qué)

- **❌ Vector store en Fase 1**: corpus < 10 docs, RAG aporta poco, añade complejidad. → Fase 2.
- **❌ MCP servers Fase 1**: Python imports directos son más simples. MCP cuando tenga sentido exponer a otros hosts. → quizás nunca.
- **❌ Postgres/Redis**: SQLite sobra para uso personal (capaz de millones de filas).
- **❌ Celery/queues**: LangGraph + asyncio paralelizan sin infraestructura adicional.
- **❌ Auth/users/multi-tenancy**: single-user local.
- **❌ Observability SaaS**: logs estructurados + LangGraph native tracing bastan hasta Fase 5.
- **❌ Docker Fase 0-1**: se introduce en Fase 5 cuando hay deploy real.
- **❌ Self-critique loops caros**: research muestra eficacia limitada; priorizamos validación post-hoc de citas (ADR-005).
- **❌ Generar frontend fancy antes de tener el engine**: Fase 5, no antes.
- **❌ Modelos locales (Ollama)**: descartado por el usuario; overhead de infra > ahorro.

---

## 9. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| API rate limits (Polygon, Finnhub) | Fase 1 bloqueada si se exceden | Caching agresivo de raw responses; retries con backoff; fallback a Alpha Vantage |
| XBRL tagging inconsistencies entre años | Números mal agregados | Pydantic validation; cross-check con texto del 10-K cuando haya anomalía |
| Restatements no detectados | Histórico incorrecto | Check 10-K/A + Form 8-K Item 4.02 antes de usar |
| Costes LLM crecen con prompt size | Budget personal | Prompt caching; especialistas con modelos baratos; Expert con Opus solo en síntesis |
| Claude Pro se pierde | Sistema depende | Provider abstraction desde día 0 (ADR-004) |
| Drift del proyecto (scope creep) | Retrasa MVP | Review al final de cada fase: ¿estamos en la ruta del roadmap? |
| LLM inventa citas bien-formateadas pero fake | Falsa confianza | Validación post-hoc por substring-match (ADR-005) |

---

## 10. Decisiones tomadas en Fase 0-1a

1. **Modelos por rol** (resuelto): Haiku o sin LLM para agentes puramente tool-lane (Financials, Market). Sonnet para especialistas con razonamiento cualitativo (Mgmt, Filings, Competitive). Opus / gpt-4o para Value Expert. Critic usa modelo distinto al Expert (si Expert=Opus → Critic=gpt-4o, y viceversa).

2. **Formato del checkpoint** (resuelto): `CheckpointInput` Pydantic serializable. CLI en Phase 1, misma interfaz adaptada a webapp en Phase 5. LangGraph pausa en SQLite, CLI recoge input, graph.invoke() reanuda — patrón nativo de LangGraph que mapea directamente a una web UI.

3. **Ticker vs CIK** (resuelto): el usuario pasa ticker (interfaz humana). El Supervisor lo resuelve a CIK en el primer paso (`Company(ticker).cik`). A partir de ahí el sistema usa CIK como identifier canónico. Ambos se guardan en `AnalysisState` y en el SQLite.

4. **Política de re-análisis** (resuelto para Phase 1, diferido delta a Phase 2): Phase 1 regenera siempre y versiona (timestamp en filename). Phase 2 implementa sección "¿Qué cambió?" comparando el nuevo análisis contra el más reciente archivado.

---

## 11. Next step

Phase 0 completada (2026-04-19). Phase 1 (1a + 1b + 1c) completada (2026-04-21). Siguiente: **Phase 2** — RAG de memoria + ChromaDB + peer ROIC comparison.

---

## 12. Referencias

**Research sintetizado (2026-04-19)**:
- [EdgarTools](https://github.com/dgunning/edgartools) — primary source XBRL parser.
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
- [XBRL Data Quality Committee](https://xbrl.us/data-quality/).
- [Polygon.io pricing](https://polygon.io/pricing).
- [Beyond the Hype: Structured Data for Financial AI](https://www.xbrl.org/beyond-the-hype-how-structured-data-can-save-ai-financial-analysis/).
- [FinTagging Benchmark](https://arxiv.org/html/2505.20650).
- [Anthropic Citations API](https://platform.claude.com/docs/en/build-with-claude/citations).
- [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) — multi-agent financial arch reference.
- [FinGPT](https://arxiv.org/html/2306.06031v2).

**Value investing canon**:
- Buffett / Munger — moats, owner earnings, margin of safety.
- Philip Fisher — 15 points.
- Peter Lynch — PEG, business understandability.
- Joel Greenblatt — Magic Formula, spin-offs.
- Howard Marks — cycle awareness.
- Ben Graham — margin of safety, NCAV.
- Terry Smith, Nick Sleep, Mohnish Pabrai, Guy Spier — quality compounders, concentration.

**Labs propios referenciados**: `mcp-multiagents-labs/lab5-multiagent-langgraph` (supervisor + paralelo), `lab6-modern-rag` (chunking, hybrid, rerank, evals), `lab7-control-plane` (authority, checkpoints, policy), `lab8-router-first-rag` (lane separation).
