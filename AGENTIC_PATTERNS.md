# Agentic Patterns — Lecciones de ingeniería de IA

> **Audiencia**: ingenieros de IA construyendo sistemas agentic.
> **Propósito**: patrones y principios extraídos de la construcción del Due Diligence System,
> transferibles a cualquier sistema multi-agente independientemente del dominio.
> **Forma**: cada patrón = concepto + ejemplo real del código + anti-patrón a evitar.

Este documento es complementario a:
- [`README.md`](README.md) — qué hace el sistema
- [`CONCISE_EXPLANATION.md`](CONCISE_EXPLANATION.md) — tour de la arquitectura
- [`DESIGN_DOCUMENT.md`](DESIGN_DOCUMENT.md) — decisiones arquitectónicas tomadas
- `docs/adr/` — los 24 ADRs individuales

A diferencia de esos, este documento es **abstracción transferible**: aprendes los patrones, los aplicas a otros proyectos.

---

## Índice

**Parte I — Fundamentos arquitectónicos**
1. Separar capa determinista de probabilística
2. Schema-first — Pydantic como contrato inter-nodos

**Parte II — Reliability (cómo no fallar)**
3. State management — TypedDict + reducers
4. Multi-concept fallback chains para datos externos
5. Stub mode / graceful degradation
6. Defense in depth — "trust but verify"
7. Auto-flag pattern — enriquecimiento proactivo del contexto

**Parte III — Abstracción y coordinación**
8. Provider abstraction — Dependency Inversion en AI
9. Send() fan-out — paralelismo dinámico real
10. `interrupt()` + resume — HITL real con persistencia

**Parte IV — Quality of reasoning**
11. Citations validadas — grounding cualitativo
12. Pre-computation in context — no le hagas hacer matemáticas al LLM
13. Context management — qué incluir es decisión arquitectónica
14. Section-structured prompts — chain-of-thought por schema
15. Critic como red team independiente

**Parte V — Operations**
16. String matching discipline — format normalization + last-match-bias
17. Structured logging + run_id correlation

**Parte VI — Meta**
18. El meta-principio
19. Anti-patrones generales

---

# Parte I — Fundamentos arquitectónicos

## 1. El principio rector — separar capa determinista de capa probabilística

**El principio**: en un sistema con LLMs, identifica qué información debe ser **deterministica** (reproducible, auditable, sin variación) vs **probabilistica** (output del LLM, puede variar entre runs). Construye dos capas separadas. Los datos nunca pasan por la capa probabilística.

### Por qué importa

Los LLMs alucinan números. Es un hecho empírico: incluso modelos frontera tienen ~17% de error mapeando números a taxonomías GAAP en finanzas. Si dejas que el LLM "lea" el ROIC de un 10-K y lo escriba en su análisis, eventualmente inventará uno.

La solución típica que NO funciona bien: validación post-hoc. "Que el LLM produzca el número, después comparamos con la fuente." El problema es que el LLM puede inventar un número plausible que pasa validación visual pero no es el real.

La solución que SÍ funciona: **arquitectura**. Los números los extrae código determinista (parser, API, cálculo). El LLM los recibe como input ya validados, los razona, los cita, pero NUNCA los genera.

### En este proyecto

| Capa | Quién está | Ejemplos de output |
|------|------------|---------------------|
| **Tool-lane (determinista)** | `financials.py`, `market.py`, `peers.py`, `valuation.py` | ROIC, FCF, revenue, DCF |
| **LLM-lane (probabilística)** | `management.py`, `filings.py`, `competitive.py`, `value_expert.py`, `critic.py` | Moat analysis, risk discussion, VERDICT |

Es ADR-005 — la decisión más crítica del sistema.

```python
# tool-lane: financials.py extrae ROIC desde XBRL
roic_series = [
    MetricValue(value=nopat / ic_value, unit="ratio", period=oi.period, source="calculated")
    for oi in op_inc_list
]
# El LLM nunca produce un value=0.40 — solo lo lee del MetricValue

# LLM-lane: value_expert.py recibe el ROIC y razona
"§2 Moat Analysis: BBW's ROIC is 40.2% (FY2026), with rising trend..."
# El "40.2%" lo cita verbatim del estado, no lo genera
```

### Anti-patrón: "el LLM con tool calling extrae los números"

```python
# ❌ ANTI-PATRÓN
@tool
def get_roic(ticker: str) -> str:
    """Get ROIC for a ticker."""
    return llm.generate(f"What is the ROIC of {ticker}?")
```

Esto es darle al LLM la responsabilidad de extraer un dato cuantitativo. Eventualmente alucinará. La forma correcta:

```python
# ✓ PATRÓN
def get_roic(ticker: str) -> MetricValue:
    raw = EdgarAdapter.fetch_raw(ticker)
    return EdgarAdapter.parse_financials(raw).roic_series[0]
# El LLM solo recibe MetricValue ya validado
```

---

## 2. Schema-first — Pydantic como contrato inter-nodos

**El patrón**: el output de cada nodo es un Pydantic model con schema fijo. Los downstream consumers conocen los campos sin leer el código del productor. **El schema ES el contrato.**

### Por qué importa

En sistemas multi-agente, los nodos están desacoplados. `value_expert` consume `competitive` sin importar cómo se generó. El schema es la frontera entre productores y consumidores: cambios internos en `competitive_agent` son libres mientras respeten el schema.

Sin schema, pasa esto: cambias el shape del output de un agente, no actualizas el consumer, runtime error días después en producción. Con schema (Pydantic + Literal types), el cambio rompe inmediatamente — el problema no se propaga.

### En este proyecto

```python
# schemas/reports.py
class CompetitiveReport(BaseModel):
    ticker: str
    stub: bool = True
    moat_classification: Optional[Literal["wide", "narrow", "none"]] = None
    moat_sources: list[str] = Field(default_factory=list)
    peer_roic_comparison: list[PeerROIC] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
```

**Tres beneficios concretos**:

1. **Contratos claros**: `value_expert` sabe que `competitive.moat_classification ∈ {"wide", "narrow", "none"}` sin leer `competitive.py`.
2. **Validación runtime**: si el LLM produce `"narrow-ish"` (no en enum), Pydantic rechaza ANTES de propagarlo al downstream.
3. **Serialización gratis**: `archiver` itera campos del modelo sin hardcodear nombres ni shapes.

### Diferencia con state management (§3)

State management trata de **cómo se mergea** entre nodos paralelos (reducers). Schema-first trata del **contrato del valor** que se almacena en cada slot. Son ortogonales:

| Garantía | Mecanismo |
|----------|-----------|
| **No colisión** | Reducers (cada agente escribe a una clave única) |
| **Type safety** | Pydantic model como valor en la clave |

Necesitas las dos.

### Anti-patrón: nodos devolviendo dicts libres

```python
# ❌ ANTI-PATRÓN
def competitive_agent(state) -> dict:
    return {
        "competitive": {
            "moat": "narrow",       # ¿el campo se llama "moat" o "moat_classification"?
            "sources": "brand,scale", # ¿es lista o string CSV?
        }
    }
```

Si `value_expert` espera `competitive.moat_classification` y recibe `competitive.moat`, el bug es silencioso hasta runtime. Pydantic + Literal previene esto.

---

# Parte II — Reliability

## 3. State management — TypedDict + reducers

**El concepto**: en LangGraph, cada nodo del grafo recibe el estado completo, hace su trabajo, y devuelve **solo las claves que modificó** (dict parcial). LangGraph aplica un **reducer** por clave para mergear los outputs en el estado global.

### El mecanismo

```python
# state.py
class AnalysisState(TypedDict):
    ticker: str
    mgmt: NotRequired[ManagementReport]
    filings: NotRequired[FilingsRAGReport]
    market: NotRequired[MarketReport]
    competitive: NotRequired[CompetitiveReport]
    # ...
```

`AnalysisState` es un `TypedDict` (dict de Python con type hints, **no** un Pydantic model). LangGraph lo usa como "pizarra compartida" del grafo.

Cuando 4 agentes corren en paralelo:

```python
# management_agent
return {"mgmt": ManagementReport(...)}    # solo escribe la clave "mgmt"

# filings_agent
return {"filings": FilingsRAGReport(...)} # solo escribe la clave "filings"

# market_agent
return {"market": MarketReport(...)}      # solo escribe la clave "market"

# competitive_agent
return {"competitive": CompetitiveReport(...)}
```

LangGraph mergea aplicando el reducer por defecto (replace = "last write wins"):

```python
state["mgmt"]        = nuevo_mgmt       # agente que escribió mgmt
state["filings"]     = nuevo_filings    # agente que escribió filings
# ...
```

**No hay colisión porque cada agente escribe en una clave distinta.**

### El bug típico que la gente sufre

Si dos agentes paralelos escriben a la **misma clave**, con default reducer (replace), uno se pierde:

```python
# ❌ ANTI-PATRÓN
class State(TypedDict):
    results: list[str]   # clave compartida

def agent_a(state) -> dict:
    return {"results": ["A"]}

def agent_b(state) -> dict:
    return {"results": ["B"]}

# En paralelo:
# state["results"] = ["A"]  ← agent_a escribe
# state["results"] = ["B"]  ← agent_b escribe, sobrescribe
# resultado: ["B"], "A" perdido
```

### La solución: reducers explícitos con `Annotated`

Si necesitas que los agentes acumulen en la misma clave, declara un reducer:

```python
from operator import add
from typing import Annotated, TypedDict

class State(TypedDict):
    results: Annotated[list[str], add]  # reducer = list concatenation

# Ahora:
# state["results"] = add(None, ["A"])    → ["A"]
# state["results"] = add(["A"], ["B"])   → ["A", "B"]
# Sin pérdidas.
```

---

## 4. Multi-concept fallback chains para datos externos

**El patrón**: cuando los datos externos vienen con vocabulario variable (XBRL concepts, API response shapes, document section names), define una **cadena priorizada de alternativas**, no un único nombre canónico.

### Por qué importa

El sistema asume "perfect XBRL" y falla silencioso cuando un filer usa concepto distinto. **No es happy-path engineering** — la realidad de los datos externos es siempre vocabulario heterogéneo. Cada API, cada formato, cada documento tiene variantes que solo descubres en producción.

### Ejemplo real — extraer Operating Income desde XBRL (EC-21)

```python
def _compute_operating_income(inc_df) -> list[dict]:
    # 1. Concepto estándar (works for AAPL, large caps)
    op_inc = _get_annual(inc_df, "OperatingIncomeLoss")
    if op_inc:
        return op_inc
    # 2. Derivado: GrossProfit - SG&A (BBW, retailers)
    gp = _get_annual(inc_df, "GrossProfit")
    sga = _get_annual(inc_df, "SellingGeneralAndAdministrativeExpense")
    if gp and sga:
        return _subtract_aligned(gp, sga)
    # 3. Pretax - Interest (variante alternativa)
    pretax = _get_annual(inc_df, "IncomeLossFromContinuing...")
    interest = _get_annual(inc_df, "InterestIncomeExpenseNonoperatingNet")
    if pretax and interest:
        return _subtract_aligned(pretax, interest)
    # 4. Pretax solo (proxy — sobreestima EBIT)
    if pretax:
        return pretax
    return []
```

### Aplicado 5+ veces en el proyecto

| Concepto | Fallbacks |
|----------|-----------|
| Operating Cash Flow | 4 conceptos XBRL |
| Capex | 4 conceptos XBRL |
| EBIT | 4 fallbacks (arriba) |
| DEF 14A markers (ownership) | 5 variantes ("Security Ownership of...", "Beneficial Ownership", "Stock Ownership"...) |
| 8-K items | normalización con `.replace("Item ", "")` |

### Diseño de la cadena: ordena por preferencia, no alfabéticamente

La clave es **el orden**: el primer match gana. Pones primero la fuente más fiable/precisa, después las menos fiables como fallbacks. La fórmula `Pretax` como último resort sobreestima EBIT (no resta interest expense), pero es mejor que devolver `[]`.

### Anti-patrón: un solo concepto, sin fallback

```python
# ❌ ANTI-PATRÓN
op_inc = _get_annual(inc_df, "OperatingIncomeLoss")
# Para BBW devuelve [] silenciosamente
# → ROIC=N/A
# → §2 Moat Analysis no puede comparar con peers
# → análisis roto sin error visible
```

Cuando trabajes con datos externos, siempre pregúntate: "¿qué pasa si esta fuente no tiene este campo exacto?" Si tu respuesta es "se rompe el sistema", necesitas fallbacks.

---

## 5. Stub mode / graceful degradation

**El patrón**: cada agente tiene un campo `stub: bool` en su output Pydantic. Si falla (API caída, datos incompletos, parsing error), devuelve un report con `stub=True` en lugar de crashear. Downstream sabe que el agente no produjo datos reales.

### Por qué importa

En pipelines multi-agente, fail-fast NO siempre es lo correcto. Si Polygon está caído, no quieres abortar el análisis — quieres que el resto continúe sin precio actual. El usuario puede aún revisar el moat, los riesgos del 10-K, las transacciones de insiders.

La diferencia con `None`/`null`: el flag `stub=True` distingue tres estados:
- **Real data**: `stub=False`, campos poblados → el agente trabajó
- **Stub**: `stub=True`, campos vacíos → el agente falló graciosamente
- **Ausente**: la clave no está en el state → el agente no se ejecutó

### En el proyecto

```python
# schemas/reports.py
class FilingsRAGReport(BaseModel):
    ticker: str
    stub: bool = True  # default = stub
    business_description: Optional[str] = None
    risk_factors_summary: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)
```

Cómo lo consumen los downstream:

```python
# value_expert.py
filings = state.get("filings")
if filings is not None and not filings.stub:
    lines.append(f"Business: {filings.business_description}")
    # Trabaja con datos reales
else:
    # Stub o ausente — manejar graciosamente, no crash
    # El §1 Business Understanding del Expert se generará con los datos disponibles
    pass
```

### Anti-patrones

- ❌ `raise Exception("Polygon failed")` que paraliza todo el pipeline
- ❌ Devolver `None` sin distinguir "no datos por error" de "campo no aplicable"
- ❌ **Stub silencioso**: devolver Pydantic model con campos vacíos pero `stub=False` — downstream piensa que son datos reales

El último es el más peligroso: el agente "tuvo éxito" pero no produjo nada útil. El downstream confía en datos vacíos. La diferencia entre "fallé y lo digo" y "fallé silenciosamente" determina si el bug se detecta o se acumula.

---

## 6. Defense in depth — "trust but verify"

**El patrón**: cuando una invariante depende del LLM (ej. "sizing debe escalar con confidence"), aplica DOS capas:
1. **Soft rail** (prompt): instrucción clara para que el LLM intente cumplir
2. **Hard rail** (código): verificación programática que **fuerza** la invariante post-hoc

Solo prompt = el LLM puede fallar y el output corrupto pasa al downstream.
Solo código = más correcciones de las necesarias, peor calidad inicial.

Las dos capas **juntas** = output correcto la mayoría de veces (prompt) + invariante garantizada siempre (clamp).

### Ejemplo: sizing clamp (EC-24)

**Bug original**: el LLM emitía `verdict_confidence=0.64, suggested_sizing_pct=3.0`. Lógicamente incoherente — bajar confidence debería implicar bajar sizing. Pero el LLM emite los dos campos independientemente.

**Soft rail** — regla en el system prompt:

```
SIZING DISCIPLINE — NON-NEGOTIABLE:
- suggested_sizing_pct MUST scale with verdict_confidence.
  Rule: max sizing ≤ verdict_confidence × 10.
  Examples: confidence 0.50 → max 5%; confidence 0.70 → max 7%.
```

Esto mejora el primer intento del LLM. ~85% de runs cumplen la regla.

**Hard rail** — clamp en `_parse_response`:

```python
# value_expert.py
sizing = max(0.0, min(20.0, float(sizing_raw)))  # primer clamp absoluto

# Sizing discipline post-hoc clamp (EC-24)
sizing_ceiling = confidence * 10.0
if sizing > sizing_ceiling:
    log.info("value_expert_sizing_clamped",
             ticker=ticker, original_sizing=sizing,
             clamped_to=sizing_ceiling, confidence=confidence)
    sizing = sizing_ceiling   # ← invariante FORZADA
```

Si el LLM emitió `confidence=0.4, sizing=8.0`:
- Ceiling = 4.0
- 8.0 > 4.0 → sizing reescrito a 4.0
- Log registra para auditoría

### Otros tres ejemplos en el proyecto

**Moat downgrade (EC-28 / ADR-024)**:
- Soft: prompt define rúbrica de 4 criterios para wide moat
- Hard: si LLM dice "wide" pero `_check_wide_moat_criteria(financials)` falla → downgrade a "narrow"

**Citation validation (ADR-005)**:
- Soft: prompt pide citar fuentes verbatim
- Hard: substring-match del excerpt contra el filing real → si no aparece, `confidence=0` y se excluye

**DCF growth cap (EC-15 / ADR-022)**:
- Soft: ninguno (es 100% código)
- Hard: `near_term_growth = min(FCF_CAGR, revenue_CAGR)` — el código fuerza el techo

### El principio mental

El LLM es **un asistente inteligente que recomienda**. Brillante pero **no autoritativo**. El **código es la autoridad** — define las invariantes que NO PUEDEN ser violadas.

Cuando diseñes:
- ¿Qué reglas son "preferencias" tolerables si el LLM las viola? → solo prompt
- ¿Qué reglas son "invariantes" que jamás pueden romperse? → soft + hard rail

### Anti-patrón: confiar en validación post-hoc del LLM

```python
# ❌ ANTI-PATRÓN
response = llm.generate(prompt)
verification = llm.generate(f"Does this comply with rule X? {response}")
if "yes" in verification.lower():
    accept(response)
```

Pedirle al LLM que valide su propio output (o el de otro LLM) NO es una invariante — es otro output probabilístico. Tiene sesgo de confirmación, puede aceptar errores. La verificación tiene que ser **código**, no más LLM.

---

## 7. Auto-flag pattern — enriquecimiento proactivo del contexto

**El patrón**: cuando una métrica cruza un threshold relevante, **el código emite automáticamente un flag** en el contexto del LLM. No esperes que el LLM lo detecte por sí solo.

### Diferencia con defense in depth (§6)

- **Defense in depth = REACTIVO**: verifica el output del LLM y corrige
- **Auto-flag = PROACTIVO**: inyecta contexto ANTES de que el LLM razone

Ambos protegen invariantes, pero en momentos distintos del flujo.

### Por qué importa

Los LLMs no detectan automáticamente patrones cross-section. Si las margins están comprimiéndose 3 años seguidos, el LLM podría notarlo o no — depende del orden en que procese los datos en su context, del tamaño del contexto, del modelo. **No es fiable.**

El código identifica el patrón con precisión, formatea como advertencia legible, y lo inyecta donde el LLM lo encontrará al razonar. Pasa de "tal vez lo nota" a "definitivamente lo ve".

### Aplicado en el proyecto

```python
# valuation.py — DCF contradiction flag
if (near_term_growth - reverse_growth) > 0.05:
    note_parts.append(
        f"⚠️ DCF contradiction: forward DCF assumes {near_term_growth:.1%} "
        f"but market prices only {reverse_growth:.1%}. The DCF upside is "
        f"conditional on growth the market doesn't currently price."
    )

# value_expert.py _format_context — margin compression flag
if all(margins[i][1] < margins[i + 1][1] for i in range(2)):
    lines.append(
        f"⚠️ STRUCTURAL MARGIN RISK: gross margin compressed 3 consecutive years "
        f"({trend_str}). Elevate to §7 Risks as 'structural margin pressure'."
    )

# critic.py _detect_red_flags — high capital return flag
if pct >= 80:
    flags.append(
        f"HIGH CAPITAL RETURN ({pct:.0f}% of FCF): the company returns nearly "
        f"all cash to shareholders. Bull narrative may frame this as 'shareholder-"
        f"friendly' but contrarian view: limited reinvestment opportunities — "
        f"a mature/declining business with no growth runway."
    )
```

Lo importante: el flag no es solo "señalar el dato" — es **interpretarlo en el lenguaje que el agente downstream necesita usar**. "STRUCTURAL MARGIN RISK" es un término que el §7 Risks del Value Expert puede citar verbatim.

### Anti-patrón: prompt-only con esperanza

```python
# ❌ ANTI-PATRÓN
prompt = """Analyze the company. By the way, also identify if margins are
compressing or if there's any structural risk pattern across the data."""
```

El LLM podría notarlo o no. Si los datos están en distintas secciones del prompt, podría no conectar. **Lo que el código puede detectar con precisión, el código debe detectarlo y comunicarlo explícitamente.**

---

# Parte III — Abstracción y coordinación

## 8. Provider abstraction — Dependency Inversion en AI

**El principio**: los proveedores de LLMs (OpenAI, Anthropic, Google, Meta) cambian APIs, precios, capacidades cada 3-6 meses. Si el código de negocio importa SDKs directamente, cada cambio = refactor masivo.

La solución: **wrapper único** con interfaz unificada. El código de negocio depende de la abstracción, no del SDK específico.

### En este proyecto

`llm/client.py` es el ÚNICO módulo que importa `anthropic` o `openai`:

```python
# llm/client.py
class LLMClient:
    def complete(
        self,
        messages: list[dict],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> str:
        # 1. Intenta Claude primero (modelo principal)
        # 2. Si Claude falla → fallback a OpenAI
        # 3. Normaliza la respuesta (cada SDK devuelve shapes distintos)
        # 4. Acumula tokens en RunStats para coste tracking (ADR-021)
        ...
```

Cada agente LLM consume así:

```python
# value_expert.py
from due_diligence.llm.client import get_client

response_text = get_client().complete(
    messages=[{"role": "user", "content": user_prompt}],
    system=_SYSTEM_PROMPT,
    model=settings.openai_expert_model,
    max_tokens=8192,
)
```

El agente NO sabe si por debajo está Claude u OpenAI. Solo ve `complete()`.

### El valor real

**Escenario**: mañana sale Gemini 3 con calidad superior a 1/3 del precio. Quieres migrar.

| Sin wrapper | Con wrapper |
|-------------|-------------|
| Tocas 5 agentes | Tocas 1 archivo (`llm/client.py`) |
| Refactor de imports, mensaje formats, response shapes | Añadir Gemini a la chain de fallback |
| Bugs sutiles probables | Cambio aislado |
| ~2 días | ~2 horas |

### Qué meter / qué NO meter en el wrapper

```
✓ Meter:
- Lazy initialization de clientes
- Fallback chain entre proveedores
- Normalización de response shapes
- Tracking de tokens y coste
- Retry logic (limitada — YAGNI)

✗ NO meter:
- Business logic ("si el ticker es BBW usa modelo X")
- Prompt templates específicos del dominio
- Validación de outputs específica del agente
- Streaming (hasta que sea necesario)
```

El wrapper es **solo abstracción de proveedor**. Si metes lógica de negocio aquí, contaminas la capa.

### Anti-patrón: importar SDK directamente desde código de negocio

```python
# ❌ ANTI-PATRÓN
# en agents/value_expert.py
import openai

def value_expert_agent(state):
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(model="gpt-4", messages=[...])
    text = response.choices[0].message.content
```

Cuando OpenAI cambie `chat.completions.create` → `responses.create` (ya pasó), o `max_tokens` → `max_completion_tokens` (ya pasó), TODOS los agentes rompen simultáneamente. Con wrapper, solo se rompe `llm/client.py` y se arregla en un solo punto.

---

## 9. Send() fan-out — paralelismo dinámico real

**El concepto**: cuando un nodo del grafo necesita despachar trabajo a múltiples nodos paralelos (con potencialmente parámetros distintos por destino), usa `Send()` en lugar de edges estáticos.

### Edges estáticos vs Send()

**Edges estáticos** (`add_edge`): A → B fijo, hardcoded.

**Send()**: una función decide a qué destinos despachar y con qué payload. Permite paralelismo dinámico (ej. "lanzar un nodo por cada elemento de una lista").

### En este proyecto

```python
# graph.py
def _route_to_specialists(state: AnalysisState) -> list[Send]:
    """Devuelve qué destinos lanzar y con qué payload."""
    return [
        Send("management", state),
        Send("filings", state),
        Send("market", state),
        Send("competitive", state),
    ]

# Conditional edge: dispatch dinámico según el estado
builder.add_conditional_edges("financials", _route_to_specialists)
```

Tras `financials_node` termina, los 4 specialists corren **en paralelo**. Cada uno con una copia del state.

### ¿"Concurrente" = paralelismo real?

Aquí hay un matiz crítico. Nuestros agentes son **sync** (`def market_agent(state)`, no `async def`). El GIL de Python en teoría serializa ejecución. Pero:

- Las llamadas a EDGAR, Polygon, Finnhub, Tavily son **I/O bound** (HTTP)
- Durante I/O, el GIL **se libera**
- LangGraph internamente usa `concurrent.futures.ThreadPoolExecutor` para nodes sync
- Los 4 specialists están **realmente esperando I/O en paralelo**

**Implicación práctica**: si el specialist más lento tarda 8s y los demás 5s:
- Secuencial: 23s
- Fan-out: max(8, 5, 5, 5) = 8s

Es paralelismo efectivo aunque no sea async puro.

### Anti-patrón: secuencial cuando podrías paralelo

```python
# ❌ ANTI-PATRÓN
builder.add_edge("financials", "management")
builder.add_edge("management", "filings")
builder.add_edge("filings", "market")
builder.add_edge("market", "competitive")
builder.add_edge("competitive", "next")
```

Esto encadena los 4 specialists secuencialmente. Cada uno espera al anterior aunque sus inputs son independientes. **Cuello de botella innecesario.**

---

## 10. `interrupt()` + resume — HITL real con persistencia

**El concepto**: pausa real del grafo para input humano, con persistencia de estado. NO es un `input()` de Python.

### El mecanismo

```python
# checkpoint.py
def checkpoint1_node(state: AnalysisState) -> dict:
    payload = build_summary_for_user(state)
    user_input = interrupt(payload)   # ← pausa aquí
    return {"checkpoint1_user_input": user_input}
```

Cuando ejecutas el grafo y llega a `interrupt()`:

1. LangGraph lanza una excepción especial `GraphInterrupt`
2. Captura la excepción, **serializa el estado completo** al checkpointer
3. Devuelve control al caller (CLI, webapp, lo que sea)
4. El caller muestra `payload` al usuario, recoge respuesta
5. El caller invoca `graph.invoke(Command(resume=user_response), config=config)`
6. LangGraph **deserializa el estado**, vuelve al punto exacto del interrupt, continúa

### La consecuencia poderosa

Podrías cerrar el terminal a mitad del análisis y reanudar mañana. El estado está persistido. Esto es lo que distingue HITL "real" de un `input()`:

| Mecanismo | Persistencia | Async safe | Tiempo máximo de espera |
|-----------|--------------|------------|-------------------------|
| `input()` de Python | Solo en memoria del proceso | No (bloquea) | Hasta que cierres el terminal |
| `interrupt()` de LangGraph | A SQLite via checkpointer | Sí | Indefinido (días, semanas) |

En sistemas profesionales (LangSmith, LangServe), esto permite humans-in-the-loop async — el usuario tarda minutos o días, el sistema resume cuando responde.

### En este proyecto

Usamos `MemorySaver` (RAM) en lugar de `SqliteSaver` por un bug de serialización con Pydantic v2 (ADR-020). En Phase 5 (webapp) se reactivará SqliteSaver cuando el bug esté arreglado.

### Anti-patrón: `input()` bloqueante o while-loop polling

```python
# ❌ ANTI-PATRÓN
def my_node(state):
    print(summary)
    response = input("Approve? Y/N: ")  # bloquea el grafo
    return {"approved": response == "Y"}
```

Esto NO es HITL escalable. No persiste, no permite async, no funciona en webapp. El patrón correcto es `interrupt()` + checkpointer.

---

# Parte IV — Quality of reasoning

## 11. Citations validadas — grounding cualitativo

**El concepto**: cada claim cualitativo del LLM viene con metadata que lo hace verificable. Validación post-hoc por substring-match contra la fuente. Si no pasa, el claim se excluye.

### El schema

```python
# schemas/base.py
class Citation(BaseModel):
    claim: str           # afirmación textual
    source_url: str      # URL del documento fuente
    excerpt: str         # fragmento verbatim del documento
    confidence: float    # 0.0-1.0, score de match
```

### El flujo

1. El LLM produce un claim + un excerpt verbatim del documento
2. Código post-hoc hace `excerpt in document_text` — substring-match exacto
3. Si match → confidence preserved, cita aceptada
4. Si NO match → confidence = 0, cita excluida del report final

```python
# Cuando el LLM emite:
{
  "claim": "Tim Cook owns 3.3M shares",
  "source_url": "https://sec.gov/.../DEF14A.htm",
  "excerpt": "Timothy D. Cook beneficially owns 3,279,321 shares",
  "confidence": 0.95
}

# Validación:
def validate_citation(c: Citation, source_text: str) -> Citation:
    if c.excerpt in source_text:
        return c  # match exacto, accept
    else:
        c.confidence = 0.0
        log.warning("citation_unverified", claim=c.claim)
        return c  # excluded by downstream filters
```

### Por qué funciona

El LLM no puede "casi citar" — el substring tiene que aparecer **literal** en la fuente. Si inventó la cita o parafraseó, el match falla. Esto cierra el loop de auditoría: cada afirmación cualitativa del report puede rastrearse a la frase exacta del documento original.

### Anti-patrón: confiar en URLs sin validar excerpt

```python
# ❌ ANTI-PATRÓN
{
  "claim": "Apple's revenue grew 8% in 2025",
  "source_url": "https://sec.gov/.../10-K.htm"
}
# Sin excerpt → no hay forma de verificar
# El LLM puede haber inventado el claim y puesto cualquier URL
```

URLs sin excerpt son trazabilidad falsa. La validación necesita el fragmento verbatim para que el substring-match funcione.

---

## 12. Pre-computation in context — no le hagas hacer matemáticas al LLM

**El patrón**: cuando una métrica derivada se puede calcular con código simple, hazlo TÚ y pásala al contexto del LLM. **No esperes que el LLM haga aritmética básica desde inputs separados.**

### Por qué importa

Dos efectos críticos:

1. **Precisión garantizada**: la división Python es exacta y reproducible; el LLM puede equivocarse, especialmente con números pequeños o porcentajes.
2. **Léxico controlado**: precomputar también significa **etiquetar**. "39% — balanced mode" introduce un término que el prompt downstream usa verbatim, en lugar de que el LLM invente sinónimos ("medium reinvestment", "moderate", "intermediate"...).

### Ejemplo real — Reinvestment rate

§5 Growth & Reinvestment necesita "reinvestment rate". Tenemos OCF y Capex en `FinancialsReport`.

**Antes** (LLM hace la cuenta):
```
Operating Cash Flow: $65M (FY2026)
Capex: $26M (FY2026)
```
El LLM se supone que calcule "26/65 = 40%, balanced mode". Pero a veces se confunde con la división, o usa terminología distinta cada run.

**Después** (precomputado):
```python
# value_expert.py _format_context
if financials.operating_cash_flow and financials.capex:
    ocf_v = financials.operating_cash_flow[0].value
    capex_v = financials.capex[0].value
    if ocf_v > 0:
        reinvest_pct = capex_v / ocf_v * 100
        mode = ("invest mode" if reinvest_pct >= 50
                else "balanced" if reinvest_pct >= 20
                else "harvest mode")
        lines.append(
            f"Reinvestment rate (capex/OCF, {period}): "
            f"{reinvest_pct:.0f}% — {mode}"
        )
```

Output que recibe el LLM:
```
Reinvestment rate (capex/OCF, FY2026): 40% — balanced
```

El §5 prompt rule downstream dice "MUST report reinvestment rate and state whether company is in invest/balanced/harvest mode" — se conecta directamente con la etiqueta precomputada.

### Aplicado en el proyecto

| Métrica | Pre-cómputo |
|---------|-------------|
| Reinvestment rate | `capex/OCF` con etiqueta invest/balanced/harvest |
| Gross margin trend | Trend siempre visible, no solo en compresión |
| ROIC trend interpretation | "RISING/STABLE/FALLING" + ΔROIC en pp/year |
| Capital returns | `(buybacks + dividends) / FCF` con `% of FCF` |

### Anti-patrón: dejar al LLM hacer aritmética

```python
# ❌ Pasar al LLM:
"Operating Cash Flow: $65M"
"Capex: $26M"
"Calculate the reinvestment rate."

# ✓ Pasar al LLM:
"Reinvestment rate (capex/OCF): 40% — balanced mode"
```

**Si lo puedes calcular con código simple, calcúlalo y dáselo masticado.** El LLM debe razonar SOBRE las métricas, no calcularlas.

---

## 13. Context management — qué incluir es decisión arquitectónica

**El concepto**: cuánto contexto darle al LLM es una decisión tan importante como el prompt. Más contexto NO siempre es mejor.

### Dos efectos contraintuitivos

**1. "Lost in the middle"**: estudios empíricos muestran que los LLMs procesan peor información que aparece **en el medio** de contextos largos. Mejor performance al inicio y al final del prompt. Si pones el dato crítico en el medio de un 10-K de 200K chars, hay menos garantía de que el LLM lo use.

**2. Coste lineal**: tokens cuestan dinero proporcionalmente. Un context de 100K tokens vs 10K es 10× más caro por llamada. Acumulado en miles de runs = significativo.

### En este proyecto

| Fuente | Original | En el contexto del LLM |
|--------|----------|------------------------|
| 10-K Item 1 (Business) | 50-100K chars | 8K chars |
| 10-K Item 1A (Risk Factors) | 30-60K chars | 10K chars |
| 10-K Item 7 (MD&A) | 40-80K chars | 10K chars |
| DEF 14A (proxy) | 200-300K chars | 24K chars (extracción targeted) |
| Earnings transcripts | ~20K chars/quarter | 5K chars/quarter, 2 quarters max |
| Tavily results | ~5K chars/result | 800 chars/result, 3 max |

### Estrategias de truncate

**1. Naive truncate**: `text[:max_chars]`. Simple, pero corta indiscriminadamente. Para 10-K: pierdes Items posteriores.

**2. Targeted extraction**: buscar markers conocidos y extraer ventana alrededor. Más complejo pero infinitamente más útil.

**3. Adaptive activation**: incluir contenido extra solo cuando lo justifica el contexto. Ej.: Tavily extended news solo se activa si el stock está en drawdown >30%. Para stocks normales, evitas tokens innecesarios.

```python
# market.py — adaptive Tavily activation
ratio = price_current / price_52w_high
if ratio < settings.market_news_drawdown_threshold:  # 0.70 default
    queries = [
        f"{company_name} stock decline guidance miss profit warning",
        f"{company_name} tariff supply chain margin compression",
        f"{company_name} regulatory risk SEC investigation",
    ]
    extra_news = TavilyAdapter.search(queries, time_range=None)
```

### Anti-patrón: enviar el documento completo

```python
# ❌ ANTI-PATRÓN
full_10k = fetch_10k_text(ticker)  # 200K chars
response = llm.complete(
    messages=[{"role": "user", "content": f"{full_10k}\n\nAnalyze this."}],
)
```

Problemas:
- Coste alto (50K+ tokens × tarifa)
- "Lost in the middle" — el dato crítico que necesitas puede estar en página 80
- Lentitud
- Saturación del context window de modelos más pequeños

La selección consciente de qué incluir es trabajo arquitectónico, no overhead.

---

## 14. Section-structured prompts — chain-of-thought por schema

**El patrón**: en lugar de pedir al LLM "analiza esta empresa", el system prompt fuerza un **schema con secciones obligatorias**, cada una con instrucciones específicas. El JSON output tiene una clave por sección.

### Por qué importa

Sin estructura forzada, los LLMs producen ensayos genéricos que tocan todos los temas sin profundizar. La estructura por secciones **fuerza chain-of-thought estructurado** — el LLM tiene que razonar específicamente sobre cada eje (moat, management, etc.) antes de poder generar la siguiente clave.

**Diferencia con CoT clásico** ("think step by step"): no es pensamiento libre. Es razonamiento **estructurado por contrato de datos** que mapea 1-a-1 con el schema downstream.

### Ejemplo real — Value Expert prompt

```python
OUTPUT SCHEMA:
{
  "business_understanding": "<§1: 3-4 sentences. What does it do, how it makes money, circle of competence>",
  "moat_analysis": "<§2: integrate ROIC evidence with qualitative moat. If peer ROIC available, MUST compare numbers>",
  "management_assessment": "<§3: skin in game + capital allocation + Q&A tone. MUST reconcile insider sale prices vs current price>",
  "financial_quality": "<§4: FCF + balance + margins. If 'Capital returned to shareholders' in context, MUST report % of FCF>",
  "growth_reinvestment": "<§5: reinvestment rate (MUST report precomputed) + ROIC trend>",
  "valuation_section": "<§6: MUST contrast reverse-DCF implied growth vs historical CAGR>",
  "risks": "<§7: include 8-K events PLUS top risks from Item 1A>",
  "psychological_lens": "<§8: why might be mispriced?>",
  "decision_frame": "<§9: MUST state thesis-breaker as concrete observable condition>",
  "verdict": "BUY | HOLD | SELL",
  ...
}
```

### Tres beneficios concretos

1. **Forzado de razonamiento por sección**: el LLM no puede saltarse §3 Management aunque le parezca menos relevante. Tiene que escribir algo en cada clave.
2. **Validación granular**: si una sección viene vacía, el código sabe exactamente cuál falta. `data.get("moat_analysis")` devuelve `None` si el LLM olvidó esa sección.
3. **Control downstream**: el archiver puede iterar las 9 secciones para generar el markdown sin lógica condicional compleja.

### Anti-patrón: open-ended

```python
# ❌ ANTI-PATRÓN
prompt = "Analyze this company comprehensively."
# Output: ensayo de 3000 palabras tocando todo superficialmente
# Difícil de extraer secciones específicas
# Imposible validar que el análisis cubrió moat o management
```

vs

```python
# ✓ Schema-driven
prompt = "Output JSON with these 9 specific sections..."
# Output: 9 análisis específicos cada uno respondiendo su pregunta concreta
# Validable, estructurado, downstream-friendly
```

---

## 15. Critic como red team independiente

**El patrón**: tener un agente separado cuyo único trabajo es **buscar fallos** en el output del agente principal. Estructuralmente independiente — modelo distinto si conviene, prompt antagónico, no recibe el razonamiento completo del agente principal.

### Por qué funciona

Si pides al Value Expert que critique su propio análisis, sesgo de confirmación garantizado. Un agente nuevo, sin haber visto el razonamiento completo (solo el VERDICT + datos), es genuinamente adversarial.

**Por qué NO es self-critique**: self-critique = el mismo LLM evaluando su propio output. Tiende a converger ("sí, mi análisis está bien"). Two-agent setup = el segundo modelo no tiene compromiso emocional/lógico con el primer output.

### En este proyecto

```python
# critic.py
_SYSTEM_PROMPT = """You are a skeptical value investing critic. Your job is NOT
to confirm the analyst's view — it is to steelman the opposite case.

Your task:
1. Identify the 3-5 most important weaknesses or blind spots in the bull case.
2. Write the strongest possible contra-thesis — a smart short-seller's argument.
3. Write a pre-mortem scenario: assume it's 3 years later and the thesis failed.

RULES:
- Be specific. Generic risks ("competition", "macro") are useless.
- Every weakness must be grounded in the data — no invented facts.
- Do not soften weaknesses with "however". State them bluntly.
- Do not repeat the bull case. The analyst already made it.
"""
```

Lo crítico: el Critic recibe **solo el VERDICT del Value Expert + datos crudos + flags estructurados**, NO el razonamiento completo:

```python
# critic.py — _build_critic_prompt
lines.append(f"## EXPERT VERDICT: {synthesis.verdict} (confidence: {confidence:.0%})")
# Solo el VERDICT — sin las 9 secciones de razonamiento
lines.append(f"Bull case summary: {synthesis.business_understanding}")  # 1 sección
lines.append(f"Moat: {synthesis.moat_analysis}")                         # 1 sección
# Datos crudos para grounding:
lines.append(f"ROIC: {roic_str}")
lines.append(f"FCF trend: {fcf_trend}")
# Flags estructurados (auto-flag pattern, §7):
flags = _detect_red_flags(state)
if flags:
    lines.append("## DETECTED FLAGS (use as pivots for contra-thesis)")
    for f in flags:
        lines.append(f"- {f}")
```

### Anti-patrón: self-critique loop

```python
# ❌ ANTI-PATRÓN
response = llm.generate(analysis_prompt)
critique = llm.generate(f"Now critique your own response: {response}")
# Sesgo de confirmación — el LLM rara vez se contradice fuertemente a sí mismo
```

El red team pattern es **"dos cabezas razonando independientemente"**, no "una cabeza talking to itself". La separación estructural es lo que evita la convergencia hacia el bull case.

---

# Parte V — Operations

## 16. String matching discipline — format normalization + last-match-bias

Dos sub-patrones aprendidos en EC-30 y EC-31, ambos sobre matching de strings con fuentes externas.

### 16.1 — Format normalization en identificadores externos

Cuando matcheas identifiers de fuentes externas (XBRL concepts, SEC item numbers, API response codes), **normaliza ambos lados antes de comparar**.

EC-30 caso real:
```python
# ❌ ANTES — silent format mismatch
items_filter = ["1.01", "2.02", "4.02", "5.02", "7.01", "8.01"]
# edgartools devuelve: ["Item 8.01", "Item 9.01"]
matching = [i for i in filed_items if i in items_filter]
# resultado: [] siempre (porque "Item 8.01" != "8.01")
# 16 filings, ningún match. Bug silencioso 5 iteraciones.
```

```python
# ✓ DESPUÉS — normalize both sides
items_normalized = {i.replace("Item ", "").strip().lower() for i in items_filter}
matching = [
    i for i in filed_items_raw
    if i.replace("Item ", "").strip().lower() in items_normalized
]
# resultado: 7 matches reales
```

**Por qué importa**: `Python's "in" con strings es exact match, no substring**. `"Item 8.01" in ["8.01"]` evalúa `False`. Si trabajas con identifiers externos, asumir formato uniforme es siempre incorrecto eventualmente.

### 16.2 — Last-match-bias en documentos con TOC

Cuando buscas una sección por marker en un documento largo, el primer match suele estar en el Table of Contents (referencia de página); la sección real está en el último match.

EC-31 caso real:
```python
# ❌ ANTES — captura el TOC
idx = text.find("Security Ownership of Certain Beneficial Owners")
# idx = 8278 → en el índice del proxy
# extracción: text[8278-500 : 8278+8000] → captura zona del TOC, no la tabla real
# Resultado: tabla de % invisible al LLM aunque está en el documento

# ✓ DESPUÉS — última ocurrencia = body
positions = []
start = 0
while True:
    idx = text.find(marker, start)
    if idx < 0:
        break
    positions.append(idx)
    start = idx + 1

last_idx = positions[-1]  # la sección real, no el TOC
# extracción: text[25422-500 : 25422+8000] → la tabla real con las %
```

**Por qué importa**: en documentos largos (proxies, 10-Ks) los markers conocidos aparecen primero en TOC y luego en el cuerpo. `text.find()` por defecto te da el primero — engañoso.

### Anti-patrón

```python
# ❌ Format-naive matching
items = obj.items  # ["Item 8.01", ...]
if "8.01" in items:  # exact match en lista — siempre False
    process(...)

# ❌ TOC-blind extraction
idx = text.find("Risk Factors")  # primer match = índice
extract_around(idx)  # captura referencia del TOC, no Item 1A
```

Cuando trabajas con strings de fuentes externas, **siempre pregúntate**: ¿el formato real es el que asumo? ¿hay TOC que envenene la primera ocurrencia? Estas dos preguntas previenen la clase entera de bugs sistémicos.

---

## 17. Structured logging + run_id correlation

**El patrón**: cada log es un evento estructurado con kwargs (no string interpolado). Cada análisis tiene un `run_id` que aparece en TODOS los logs y como thread_id del checkpointer.

### En el proyecto

```python
# Cada log línea es un evento con kwargs
log = structlog.get_logger()

log.info("market_drawdown_detected",
         ticker=ticker,
         drawdown_pct=f"{drawdown_pct:.0f}%",
         triggering_extended_news=True)

log.warning("competitive_moat_downgraded_wide_to_narrow",
            ticker=ticker,
            reasons=downgrade_reasons)

log.info("value_expert_sizing_clamped",
         ticker=ticker,
         original_sizing=sizing,
         clamped_to=sizing_ceiling,
         confidence=confidence)
```

### Por qué crítico para sistemas agentic

Cuatro razones específicas que NO existen en código tradicional sync:

1. **Logs intercalados**: cuando 4 agentes corren en paralelo, los logs se mezclan en el output. Sin `run_id` consistente es imposible reconstruir un análisis específico.

2. **Filtrabilidad**: `jq 'select(.run_id == "51ae8e59-466")' < logs.json` filtra todos los eventos de ese análisis. Imposible si los logs son strings.

3. **Auditoría de decisiones automáticas**: cada clamp, downgrade, flag deja rastro estructurado, no string libre. Saber por qué BBW se downgrade de wide a narrow es trivial: `grep moat_downgraded run_id=51ae8e59`.

4. **Métricas agregadas**: `event=competitive_moat_downgraded_wide_to_narrow` te dice cuántas veces el clamp se activó esta semana. Análisis de patrones, no solo debugging individual.

### Anti-patrón: print() o logging con strings

```python
# ❌ ANTI-PATRÓN
print(f"BBW market drawdown {drawdown_pct}%")
# o:
logger.info(f"BBW market drawdown {drawdown_pct}%")
# Imposible filtrar por ticker, parsear el porcentaje, agregar
# Cuando 4 agentes paralelos generan logs: contexto perdido
```

```python
# ✓ Structured
log.info("market_drawdown_detected",
         ticker=ticker, drawdown_pct=drawdown_pct, run_id=run_id)
# Filtrable por cualquier kwarg, parseable como JSON, agregable
```

**Implementación en el proyecto**: structlog con processors que añaden `run_id` automáticamente desde context vars. Output JSON en producción, console-friendly en dev.

---

# Parte VI — Meta

## 18. El meta-principio — separar lo que cambia rápido de lo que es estable

Si miras los 17 patrones anteriores, todos comparten una idea unificadora:

> **Identifica dónde está la fricción del cambio en tu sistema y confínala a una capa específica.**

| Patrón | Lo que cambia rápido | Lo estable |
|--------|----------------------|------------|
| 1. Capas determinista/probabilística | Output del LLM (varía run a run) | Schema de los datos (Pydantic) |
| 2. Schema-first | Implementación de cada agente | Contrato Pydantic entre nodos |
| 3. State + reducers | Lógica interna de cada agente | Contrato del state (TypedDict) |
| 4. Multi-concept fallbacks | Vocabulario del proveedor (XBRL concepts) | Interfaz del extractor (`get_operating_income`) |
| 5. Stub mode | Disponibilidad de cada API externa | Contrato del agente (`stub: bool`) |
| 6. Defense in depth | Decisiones del LLM (ignora reglas) | Invariantes del código (clamps) |
| 7. Auto-flag | Tipos de patrones a detectar | Mecanismo de inyección al contexto |
| 8. Provider abstraction | SDK del proveedor (cada 3 meses) | Interfaz `complete()` |
| 9. Send() fan-out | Qué nodos despachar (depende del estado) | Estructura del grafo |
| 10. interrupt() + resume | Tiempo de respuesta del usuario (días) | Estado del grafo (persistido) |
| 11. Citations validadas | Lo que el LLM afirma (varía run a run) | Las fuentes primarias (filings, APIs) |
| 12. Pre-computation | Aritmética del LLM (puede equivocarse) | Cálculo Python (exacto) |
| 13. Context management | El documento completo | El extracto relevante (markers fijos) |
| 14. Section-structured prompts | Output libre del LLM | Schema con secciones obligatorias |
| 15. Critic red team | Sesgo del agente principal | Independencia estructural del Critic |
| 16. String matching discipline | Formato real de fuentes externas | Logic de matching tolerante |
| 17. Structured logging | Eventos individuales (miles por día) | Esquema de logs (filtrable, agregable) |

**Los buenos sistemas agentic son los que identifican la fricción y la aíslan.** Los malos mezclan capas.

### Cómo aplicarlo a un sistema nuevo

Cuando empieces un proyecto agentic, antes de escribir código pregúntate:

1. **¿Qué información debe ser determinista?** → tool-lane
2. **¿Qué razonamiento es probabilístico?** → LLM-lane
3. **¿Qué interfaces externas pueden cambiar?** → wrapper / abstraction
4. **¿Qué invariantes nunca pueden romperse?** → clamps post-hoc
5. **¿Qué métricas derivadas se pueden precomputar?** → pre-computation
6. **¿Qué patrones cross-section son detectables por código?** → auto-flags
7. **¿Dónde necesito paralelismo?** → fan-out con Send()
8. **¿Dónde necesito intervención humana?** → interrupt() + checkpointer
9. **¿Cómo audito los claims cualitativos?** → citations validadas
10. **¿Qué contexto es realmente relevante?** → targeted extraction
11. **¿Cómo evito sesgo de confirmación en validación?** → red team agent
12. **¿Cómo trazo runs en producción?** → structured logging con run_id

Si respondes estas 12 preguntas antes de codificar, evitas el 80% de los problemas que aparecen después.

---

## 19. Anti-patrones generales

Recopilación de anti-patrones, organizados por categoría. Algunos ya aparecieron en sus secciones; aquí se consolidan junto con anti-patrones meta extraídos del proceso de iteración del proyecto.

### En arquitectura

- ❌ Dejar que el LLM extraiga datos cuantitativos via tool calling (alucinará)
- ❌ Mezclar capa determinista con probabilística en el mismo agente
- ❌ Validar output del LLM con otro LLM (sesgo de confirmación, no es invariante)
- ❌ Confiar solo en validación post-hoc para alucinación numérica (necesita prevención por arquitectura)
- ❌ Self-critique loops para validación adversarial (converge al bull case, no es genuinamente crítico)

### En state management y contratos

- ❌ Dos agentes paralelos escribiendo a la misma clave sin reducer (silent overwrite)
- ❌ Pasar el state completo entre nodos en lugar de devolver dict parcial
- ❌ Usar Pydantic model directamente como AnalysisState (LangGraph espera TypedDict)
- ❌ Asumir que Pydantic previene colisiones de paralelismo (solo previene shapes inválidas)
- ❌ Nodos que devuelven `dict` libre sin schema — el downstream tiene que adivinar

### En provider integration y abstracción

- ❌ Importar SDK del proveedor desde código de negocio (acoplamiento alto)
- ❌ Asumir que el SDK no cambiará (SÍ cambia, cada 3 meses)
- ❌ Meter business logic en el wrapper (contamina la capa)
- ❌ Hardcodear API keys en código (usar pydantic-settings)

### En prompts y validación

- ❌ Reglas críticas SOLO en el prompt sin clamp post-hoc
- ❌ Decir al LLM "no alucines" como mecanismo anti-alucinación
- ❌ Citations sin excerpt verbatim (URL sola = trazabilidad falsa)
- ❌ Confiar en LLM-as-judge para invariantes (es probabilístico)
- ❌ Prompts open-ended para tareas analíticas complejas (sin sección obligatoria, no hay chain-of-thought)
- ❌ Dejar al LLM hacer aritmética básica desde inputs separados (precomputa y dale el resultado)

### En context management y datos externos

- ❌ Enviar documento completo al LLM (lost in the middle + coste)
- ❌ Truncate naive `text[:N]` cuando hay markers identificables
- ❌ Buscar markers con `.find()` sin considerar TOC bias (primer match suele ser índice)
- ❌ Asumir formato uniforme en identifiers externos (XBRL concepts, item numbers, etc. — siempre normaliza)
- ❌ Un solo concepto de extracción sin fallback chain (silent failure cuando el formato varía)
- ❌ Activar todas las herramientas siempre (innecesario y caro — usar adaptive activation)

### En reliability y errores

- ❌ `raise Exception()` que paraliza todo el pipeline cuando un agente falla
- ❌ Devolver `None` sin distinguir "no datos por error" de "campo no aplicable"
- ❌ Stub silencioso (datos vacíos sin marker `stub=True`) — peor que crash explícito
- ❌ Detección reactiva ("a ver si el LLM lo nota") en lugar de auto-flag proactivo

### En observabilidad

- ❌ `print()` o logging con strings interpolados (no filtrable, no agregable)
- ❌ Logs sin `run_id` consistente (imposible correlar análisis específicos)
- ❌ No diferenciar tool-lane vs LLM-lane en métricas (mezclan latencias y costes)
- ❌ No trackear coste por llamada (se desboca rápidamente con modelos premium)

### Meta — sobre el proceso de desarrollo

- ❌ **Diseñar/testear contra "AAPL ideal"**: large cap con XBRL completo, equity grande, news flow constante. La realidad del universo es small caps con income statements simplificados, equity reducido por buybacks, news fuera de ventana. Cada Oleada de mejoras del proyecto descubrió otra dimensión de este sesgo (EC-14 a EC-33). Mitigación: test fixtures multi-arquetipo (MCE-25 — pendiente).

- ❌ **No verificar contra DATOS REALES antes de declarar fix completo**: cuando expandes extracción de datos externos, corre una vez contra una fixture viva y verifica el formato real de los outputs. El bug EC-30 (8-K filter `"Item 8.01" vs "8.01"`) sobrevivió 5 iteraciones porque la lógica era sintácticamente correcta pero el formato real del output de edgartools era distinto al asumido. 30 segundos de verificación habrían atrapado el bug en su origen.

- ❌ **Búsqueda manual de patrones duplicados al arreglar bugs**: cuando arreglas un bug en un archivo, ejecuta `grep` del patrón problemático en TODO `src/`. EC-14 (formatter `/1e9`) se arregló en `value_expert.py` y `archiver.py` pero olvidamos `critic.py` y `competitive.py`. El bug regresó como EC-17 una iteración después.

- ❌ **`replace_all` sin verificación post-hoc**: en docs y código, `replace_all` puede sobreaplicarse. Siempre revisar el diff después.

---

## Referencias cruzadas

| Patrón aquí | ADR principal | Edge cases relacionados |
|-------------|---------------|--------------------------|
| 1. Capa determinista/probabilística | ADR-005 | EC-26 (incremental ROIC), EC-32 |
| 2. Schema-first | ADR-005 | (sin EC, propio del framework) |
| 3. State management | — | (sin EC, propio del framework) |
| 4. Multi-concept fallbacks | ADR-022, ADR-023 | EC-01, EC-21, EC-25, EC-30 |
| 5. Stub mode | — | (transversal, sin EC específico) |
| 6. Defense in depth | ADR-022, ADR-024 | EC-15, EC-24, EC-28 |
| 7. Auto-flag pattern | ADR-022 (DCF flag), ADR-024 | EC-16, EC-22, EC-26 |
| 8. Provider abstraction | ADR-004 | (refactoring continuo) |
| 9. Send() fan-out | ADR-003 | EC-04 (peer comparison timing) |
| 10. interrupt() + resume | ADR-006, ADR-018, ADR-020 | EC-09 (resume con dict vacío) |
| 11. Citations validadas | ADR-005, ADR-014 | EC-23 (citations en context) |
| 12. Pre-computation | ADR-022 (EBIT), ADR-023 (capital returns) | EC-22, EC-26, EC-32 |
| 13. Context management | ADR-019 | EC-22, EC-25, EC-31 |
| 14. Section-structured prompts | ADR-019 (prompt v2) | (transversal en agentes LLM) |
| 15. Critic como red team | ADR-009 | (transversal) |
| 16. String matching discipline | — | EC-30 (format mismatch), EC-31 (TOC bias) |
| 17. Structured logging | ADR-021 | (transversal en debugging) |

---

## Ampliaciones futuras

Este documento es vivo. Patrones a añadir cuando se aprendan en producción:

- **Testing con fixtures cacheados** — zero API calls, deterministic
- **Cost observability avanzada** — alertas, presupuestos por análisis, dashboards
- **Prompt caching** (Anthropic-specific) — reducir tokens de inputs estables
- **Streaming** — cuando importe la latencia percibida
- **Multi-arquetipo testing** — MCE-25, validar fixes contra clases de empresas
- **Memory en agentes** — in-context vs short-term DB vs vector store
- **Adaptive routing entre modelos** — decidir entre Haiku/Sonnet/Opus según complejidad estimada de la query
- **Retry con backoff inteligente** — distinguir errores transitorios (rate limit) de permanentes (bad request)

Cuando un nuevo patrón se cristalice tras varias iteraciones reales, se añade aquí.

---

*Última actualización: 2026-05-10. 17 patrones + meta-principio + anti-patrones consolidados. Documento vivo — añadir patrones cuando se aprendan en producción.*
