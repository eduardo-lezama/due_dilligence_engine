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

## 2. State management — por qué los agentes paralelos no se pisan

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
return {"competitive": CompetitiveReport(...)}  # solo escribe la clave "competitive"
```

LangGraph mergea aplicando el reducer por defecto (replace = "last write wins"):

```python
state["mgmt"]        = nuevo_mgmt       # agente que escribió mgmt
state["filings"]     = nuevo_filings    # agente que escribió filings
state["market"]      = nuevo_market     # agente que escribió market
state["competitive"] = nuevo_competitive
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

### Aclaración importante: Pydantic NO previene la colisión

| Garantía | Mecanismo | Si lo violas... |
|----------|-----------|-----------------|
| **No colisión** | Cada agente escribe a una clave única | Dos agentes en la misma key sin reducer → uno perdido |
| **Type safety** | Pydantic model como valor | Agente devuelve shape malformada → ValidationError |

Son ortogonales. Pydantic protege la **forma del valor**; el reducer protege el **slot**. Los dos importan, pero no se sustituyen.

---

## 3. Defense in depth — "trust but verify"

**El patrón**: cuando una invariante depende del LLM (ej. "sizing debe escalar con confidence"), aplica DOS capas:
1. **Soft rail** (prompt): instrucción clara para que el LLM intente cumplir
2. **Hard rail** (código): verificación programática que **fuerza** la invariante post-hoc

Solo prompt = el LLM puede fallar y el output corrupto pasa al downstream.
Solo código = más correcciones de las necesarias, peor calidad inicial.

Las dos capas **juntas** = output correcto la mayoría de veces (prompt) + invariante garantizada siempre (clamp).

### Ejemplo: sizing clamp (EC-24)

**Bug original**: el LLM emitía `verdict_confidence=0.64, suggested_sizing_pct=3.0`. Lógicamente incoherente — bajar confidence debería implicar bajar sizing. Pero el LLM emite los dos campos independientemente sin consciencia de la relación.

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
    log.info(
        "value_expert_sizing_clamped",
        ticker=ticker,
        original_sizing=sizing,
        clamped_to=sizing_ceiling,
        confidence=confidence,
    )
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
# "Por favor verifica si la respuesta cumple la regla X"
verification = llm.generate(f"Does this comply? {response}")
if "yes" in verification.lower():
    accept(response)
```

Pedirle al LLM que valide su propio output (o el de otro LLM) NO es una invariante — es otro output probabilístico. Tiene sesgo de confirmación, puede aceptar errores. La verificación tiene que ser **código**, no más LLM.

---

## 4. Provider abstraction — Dependency Inversion en AI

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

def get_client() -> LLMClient:
    """Singleton — mismo objeto en todo el proceso."""
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

**Escenario hipotético**: mañana sale Gemini 3 con calidad superior a 1/3 del precio. Quieres migrar.

| Sin wrapper | Con wrapper |
|-------------|-------------|
| Tocas 5 agentes (cada uno con su SDK) | Tocas 1 archivo (`llm/client.py`) |
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
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[...]
    )
    text = response.choices[0].message.content
    ...
```

Cuando OpenAI cambie `chat.completions.create` → `responses.create` (ya pasó), o `max_tokens` → `max_completion_tokens` (ya pasó), TODOS los agentes rompen simultáneamente. Con wrapper, solo se rompe `llm/client.py` y se arregla en un solo punto.

---

## 5. Fan-out con Send() — paralelismo dinámico real

**El concepto**: cuando un nodo del grafo necesita despachar trabajo a múltiples nodos paralelos (con potencialmente parámetros distintos por destino), usa `Send()` en lugar de edges estáticos.

### Edges estáticos vs Send()

**Edges estáticos** (`add_edge`):
- A → B fijo, hardcoded
- LangGraph ejecuta concurrentemente cuando hay múltiples salidas
- Limitación: no puedes pasar parámetros distintos a cada destino

**Send()** (dinámico):
- Una función decide a qué destinos despachar y con qué payload
- Permite paralelismo dinámico (ej. "lanzar un nodo por cada elemento de una lista")
- LangGraph ejecuta concurrentemente y mergea automáticamente

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

```python
# ✓ PATRÓN
builder.add_conditional_edges("financials", _route_to_specialists)
for s in specialists:
    builder.add_edge(s, "next")  # fan-in automático
```

---

## 6. HITL real con `interrupt()` y resume

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

### Anti-patrón: input() bloqueante o while-loop polling

```python
# ❌ ANTI-PATRÓN
def my_node(state):
    print(summary)
    response = input("Approve? Y/N: ")  # bloquea el grafo
    return {"approved": response == "Y"}
```

Esto NO es HITL escalable. No persiste, no permite async, no funciona en webapp. El patrón correcto es `interrupt()` + checkpointer.

---

## 7. Citations validadas — grounding cualitativo

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

### En este proyecto

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

# Solo citations con confidence > threshold llegan al report final
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

## 8. Context management — qué incluir es decisión arquitectónica

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

**1. Naive truncate**: `text[:max_chars]`
- Simple, pero corta indiscriminadamente
- Para 10-K: pierdes Items posteriores (1A está después de 1)

**2. Targeted extraction**: buscar markers conocidos y extraer ventana alrededor
- Más complejo pero infinitamente más útil
- Ejemplo en este proyecto: DEF 14A targeted extraction de la sección de ownership

```python
# fetch_def14a_text — busca markers, extrae ventana
ownership_markers = [
    "Security Ownership of Certain Beneficial Owners",
    "Beneficial Ownership",
    "Stock Ownership",
]
for marker in ownership_markers:
    positions = find_all(text, marker)
    if positions:
        # Última ocurrencia = sección real (no TOC)
        last_idx = positions[-1]
        sections.append(text[last_idx-500 : last_idx+8000])
        break
```

**3. Pre-summarization**: una primera pasada del LLM resume el documento, después el agente principal trabaja sobre el resumen
- Caro (dos pasadas) pero útil cuando el dato relevante está distribuido

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

La selección consciente de qué incluir es trabajo arquitectónico, no overhead. Cuando el sistema "no detecta X", a menudo el problema NO es el prompt, es que X está en el contexto pero **demasiado lejos** del razonamiento.

---

## 9. El meta-principio — separar lo que cambia rápido de lo que es estable

Si miras los 8 patrones anteriores, todos comparten una idea unificadora:

> **Identifica dónde está la fricción del cambio en tu sistema y confínala a una capa específica.**

| Patrón | Lo que cambia rápido | Lo estable |
|--------|----------------------|------------|
| 1. Capas determinista/probabilística | Output del LLM (varía run a run) | Schema de los datos (Pydantic models) |
| 2. State + reducers | Lógica interna de cada agente | Contrato del state (TypedDict + reducers) |
| 3. Defense in depth | Decisiones del LLM (ignora reglas) | Invariantes del código (clamps) |
| 4. Provider abstraction | SDK del proveedor (cambia trimestralmente) | Interfaz `complete()` |
| 5. Send() fan-out | Qué nodos despachar (depende del estado) | Estructura del grafo |
| 6. interrupt() + resume | Tiempo de respuesta del usuario (días) | Estado del grafo (persistido) |
| 7. Citations validadas | Lo que el LLM afirma (cambia entre runs) | Las fuentes primarias (filings, APIs) |
| 8. Context management | El documento completo (cambia con cada filing) | El extracto relevante (markers fijos) |

**Los buenos sistemas agentic son los que identifican la fricción y la aíslan.** Los malos mezclan capas — dejan que el LLM "decida" lo que debería ser determinista, o "valide" lo que debería ser un check programático.

### Cómo aplicarlo a un sistema nuevo

Cuando empieces un proyecto agentic, antes de escribir código pregúntate:

1. **¿Qué información debe ser determinista?** → tool-lane
2. **¿Qué razonamiento es probabilístico?** → LLM-lane
3. **¿Qué interfaces externas pueden cambiar?** → wrapper / abstraction
4. **¿Qué invariantes nunca pueden romperse?** → clamps post-hoc
5. **¿Dónde necesito paralelismo?** → fan-out con Send()
6. **¿Dónde necesito intervención humana?** → interrupt() + checkpointer
7. **¿Cómo audito los claims cualitativos?** → citations validadas
8. **¿Qué contexto es realmente relevante?** → targeted extraction, no naive truncate

Si respondes estas 8 preguntas antes de codificar, evitas el 80% de los problemas que aparecen después.

---

## 10. Anti-patrones generales (qué evitar)

Recopilación de anti-patrones que NO debes cometer en sistemas agentic, extraídos de los anteriores:

### En arquitectura

- ❌ Dejar que el LLM extraiga datos cuantitativos via tool calling (alucinará)
- ❌ Mezclar capa determinista con probabilística en el mismo agente
- ❌ Validar output del LLM con otro LLM (sesgo de confirmación, no es invariante)
- ❌ Confiar solo en validación post-hoc para alucinación numérica (necesita prevención por arquitectura)

### En state management

- ❌ Dos agentes paralelos escribiendo a la misma clave sin reducer (silent overwrite)
- ❌ Pasar el state completo entre nodos en lugar de devolver dict parcial
- ❌ Usar Pydantic model directamente como AnalysisState (LangGraph espera TypedDict)
- ❌ Asumir que Pydantic previene colisiones (solo previene shapes inválidas)

### En provider integration

- ❌ Importar SDK del proveedor desde código de negocio (acoplamiento alto)
- ❌ Asumir que el SDK no cambiará (SÍ cambia, cada 3 meses)
- ❌ Meter business logic en el wrapper (contamina la capa)
- ❌ Hardcodear API keys en código (usar pydantic-settings)

### En prompts y validación

- ❌ Reglas críticas SOLO en el prompt sin clamp post-hoc
- ❌ Decir al LLM "no alucines" como mecanismo anti-alucinación
- ❌ Citations sin excerpt verbatim (URL sola = trazabilidad falsa)
- ❌ Confiar en LLM-as-judge para invariantes (es probabilístico)

### En context management

- ❌ Enviar documento completo al LLM (lost in the middle + coste)
- ❌ Truncate naive `text[:N]` cuando hay markers identificables
- ❌ No precomputar transformaciones simples (margin trends, ratios) y dejar que el LLM las calcule
- ❌ Surfacear datos a LLM solo cuando hay anomalía (también cuando todo está bien — el LLM necesita ambos lados para razonar)

### En observabilidad y testing

- ❌ Tests con llamadas reales a LLMs (caros, no deterministas, lentos)
- ❌ Logs sin contexto estructurado (structlog con kwargs es estándar)
- ❌ No trackear coste por llamada (se desboca rápidamente)
- ❌ No diferenciar tool-lane vs LLM-lane en métricas

---

## Referencias cruzadas

| Patrón aquí | ADR principal | Edge cases relacionados |
|-------------|---------------|--------------------------|
| 1. Capa determinista/probabilística | ADR-005 | EC-26 (incremental ROIC), EC-32 |
| 2. State management | — | (sin EC, propio del framework) |
| 3. Defense in depth | ADR-022, ADR-024 | EC-15, EC-24, EC-28 |
| 4. Provider abstraction | ADR-004 | (refactoring continuo) |
| 5. Send() fan-out | ADR-003 | EC-04 (peer comparison timing) |
| 6. interrupt() + resume | ADR-006, ADR-018, ADR-020 | EC-09 (resume con dict vacío) |
| 7. Citations validadas | ADR-005, ADR-014 | EC-23 (citations en context) |
| 8. Context management | ADR-019 | EC-22, EC-25, EC-31 |

---

## Ampliaciones futuras

Este documento es vivo. Patrones a añadir cuando se aprendan en producción:

- **Testing con fixtures cacheados** — zero API calls, deterministic
- **Cost observability** — RunStats, alertas, presupuestos por análisis
- **Prompt caching** (Anthropic-specific) — reducir tokens de inputs estables
- **Streaming** — cuando importe la latencia percibida
- **Multi-arquetipo testing** — MCE-25, validar fixes contra clases de empresas
- **Memory en agentes** — in-context vs short-term DB vs vector store

Cuando un nuevo patrón se cristalice tras varias iteraciones reales, se añade aquí.

---

*Última actualización: 2026-05-10. Documento vivo — añadir patrones cuando se aprendan en producción.*
