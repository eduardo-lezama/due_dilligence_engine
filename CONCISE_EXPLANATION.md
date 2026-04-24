# Due Diligence Engine — Guía de Estudio

Una explicación clara del sistema: qué es, por qué funciona así, y qué conceptos de arquitectura agentic están detrás.

---

## 1. El problema que resuelve

Hacer análisis fundamental de una empresa es un proceso de síntesis multi-fuente: lees el 10-K, revisas los proxies, miras los números históricos, buscas noticias recientes, valoras con DCF. Cada fuente responde una pregunta diferente.

Preguntarle todo esto a un LLM en un solo prompt tiene tres problemas:

1. **Alucinación numérica**: el LLM inventa ROICs, revenues o márgenes que suenan plausibles pero son incorrectos.
2. **Contexto limitado**: un 10-K tiene 200 páginas. Filings, noticias, transacciones Form 4 y análisis competitivo no caben juntos en un contexto manejable.
3. **Síntesis sin estructura**: sin un proceso explícito, el LLM produce un ensayo genérico, no un análisis de inversión accionable.

La solución no es un prompt más largo. Es una **arquitectura**.

---

## 2. La arquitectura: LangGraph + Agentes Especializados

El sistema usa **LangGraph** para orquestar un grafo de nodos donde cada nodo es un agente especializado.

### ¿Qué es LangGraph?

Un framework para construir grafos de procesamiento con estado. Piénsalo como un pipeline donde:

- Cada **nodo** hace una cosa: fetcha datos, llama a un LLM, pausa para input humano.
- El **estado** es un diccionario compartido (`AnalysisState`) que todos los nodos leen y en el que escriben solo sus claves propias.
- Los **edges** definen el flujo: A → B, o un fan-out de A → [B, C, D, E] en paralelo.

```
supervisor → financials → [management, filings, market, competitive]
                                          ↓
                               checkpoint1 → peers → checkpoint2
                                          ↓
                       valuation → value_expert → critic → archiver
```

Cada nodo recibe el estado completo, hace su trabajo, y devuelve **solo las claves que modificó**. LangGraph hace el merge automáticamente.

### Por qué este diseño y no un solo agente

Cada especialista tiene un **prompt optimizado para una sola pregunta**:
- Management: ¿quién dirige la empresa y tiene skin in the game?
- Filings: ¿qué dice el 10-K sobre el negocio y los riesgos?
- Competitive: ¿hay moat? ¿se está erosionando?
- Market: ¿a qué precio cotiza y qué eventos recientes hay?

Un agente que intenta responder las cuatro preguntas a la vez produce respuestas mediocres en todas. La especialización mejora la calidad de cada respuesta y permite paralelismo real: los cuatro corren a la vez.

---

## 3. El principio más importante: control de alucinación por arquitectura

**ADR-005** es la decisión más crítica del sistema: *todos los números vienen de herramientas deterministas, nunca del LLM*.

El ROIC, el revenue, el FCF, la deuda — todos salen de **EDGAR XBRL** vía `edgartools`. El LLM los lee, los razona, los compara, pero nunca los genera.

Esto no es una validación post-hoc. Es un **contrato de arquitectura**:

```
tool-lane (sin LLM):  financials_node, market_agent, peers_node
LLM-lane:             management_agent, filings_agent, competitive_agent, value_expert
```

Los agentes tool-lane solo llaman a APIs deterministas. Los agentes LLM-lane reciben los números como input y producen texto con citations validadas. Si el LLM intenta inventar un número, el sistema no tiene mecanismo de validación individual — pero como los números nunca entran por el LLM, el problema no existe.

### Citations: la segunda capa de control

Cada claim cualitativo del LLM viene acompañado de una `Citation`:

```python
Citation(
    claim="Tim Cook owns 3.3M shares",
    source_url="https://sec.gov/.../DEF14A.htm",
    excerpt="Timothy D. Cook beneficially owns 3,279,321 shares",
    confidence=0.95
)
```

El excerpt es verbatim del documento fuente. Si el substring-match falla (el excerpt no aparece en el filing real), la confidence cae a 0 y la cita se excluye. Esto cierra el loop de trazabilidad: cada afirmación puede rastrearse hasta su fuente primaria.

---

## 4. HITL: Human-in-the-Loop con interrupt()

El sistema no es un pipeline que corre solo hasta el final. Tiene dos puntos de pausa donde el usuario aporta información que el sistema no puede inferir solo.

**¿Por qué interrupt() y no un simple input()?**

`interrupt()` es el mecanismo de LangGraph para pausar el grafo, serializar el estado completo al checkpointer, y devolver el control al llamador. Cuando el usuario responde, el grafo se reanuda exactamente donde lo dejó — con el mismo estado, sin perder nada.

```python
user_input = interrupt(summary)  # el grafo pausa aquí
# ... días después si hace falta ...
graph.invoke(Command(resume=user_input), config=config)  # el grafo continúa
```

**Checkpoint 1 — Revisión de especialistas + Socratic**

Después de que los 4 especialistas corren en paralelo, el sistema pausa y presenta al usuario:

1. Un resumen de los resultados (ROIC, moat, precio, management).
2. Preguntas Socratic generadas a partir de los datos: si el moat es "narrow" pero el ROIC es 87%, el sistema pregunta "¿qué reconciliaría esto?". Estas preguntas no son genéricas — dependen de los datos reales del análisis.
3. La opción de aportar peer tickers para comparar ROIC.

**Checkpoint 2 — Assumptions del DCF**

Antes de la valoración, el sistema muestra el CAGR histórico de revenue (calculado desde XBRL, no inventado) y pide que el usuario justifique sus assumptions de growth. Si propones 15% de crecimiento para una empresa con CAGR histórico del 3%, tienes que articular por qué.

Este diseño implementa el doble objetivo del sistema: **decidir** (el pipeline produce un VERDICT) y **aprender** (los checkpoints fuerzan articulación consciente).

---

## 5. El Value Expert: síntesis sobre rúbrica

El Value Expert es el único agente que ve el output de todos los demás. Su trabajo no es resumir cada report — es **integrar y adjudicar conflictos**.

El prompt v2 tiene tres reglas duras:
1. Debe nombrar al menos 2 señales conflictivas y adjudicarlas explícitamente.
2. §6 Valuation debe contrastar el implied growth del reverse DCF contra el CAGR histórico.
3. §9 Decision Frame debe nombrar un thesis-breaker observable y concreto.

**¿Por qué el reverse DCF y el CAGR histórico juntos?**

El reverse DCF pregunta: *dado el precio actual, ¿qué tasa de crecimiento está descontando el mercado?* Si la respuesta es 15% y la empresa ha crecido un 3% históricamente, el mercado está pagando por una aceleración que no tiene precedente. Ese spread es la decisión de inversión más crítica — y sin el CAGR histórico como anchor, el implied growth es un número flotando en el vacío.

---

## 6. Pydantic como contrato de datos

Cada output de cada nodo es un modelo Pydantic validado:

```python
class CompetitiveReport(BaseModel):
    moat_classification: Optional[str]  # "wide", "narrow", "none"
    peer_roic_comparison: list[PeerROIC]
    citations: list[Citation]
```

Esto no es burocracia — tiene tres beneficios concretos:

1. **Contratos claros entre nodos**: si `competitive_agent` devuelve un `CompetitiveReport`, `value_expert_agent` sabe exactamente qué campos existen sin leer el código del agente anterior.
2. **Validación en runtime**: si el LLM devuelve un moat_classification de "narrow-ish" (no en el enum), Pydantic lo rechaza antes de que llegue al Expert.
3. **Serialización a SQLite y markdown**: el archiver puede iterar sobre los campos del modelo sin hardcodear nada.

---

## 7. Por qué MemorySaver y no SqliteSaver

LangGraph permite varios backends de checkpointer. SqliteSaver persiste el estado a disco — útil para reanudar entre sesiones. MemorySaver lo guarda en RAM — se pierde al salir del proceso.

El sistema usa MemorySaver porque SqliteSaver serializa el estado con msgpack, y msgpack no puede serializar objetos Pydantic v2 sin un serializer custom. El intento en una sesión anterior causó un crash.

La persistencia real ya está cubierta por el archiver: el reporte markdown y la fila en SQLite se escriben al final. Para un CLI de análisis de <3 minutos, MemorySaver es suficiente. SqliteSaver se activará en Phase 5 (webapp) cuando haya necesidad real de reanudar entre sesiones de browser.

Este es el patrón del proyecto entero: **no construir componentes cuyo ROI no se puede demostrar hoy**.

---

## 8. Las decisiones que no se ven

Algunos ADRs son de lo que se decidió **no hacer**:

**No RAG en Phase 1-2**: con < 10 análisis archivados, un vector store sobre corpus propio devuelve resultados de baja calidad. El RAG tiene valor real cuando hay ≥3 análisis del mismo ticker — el delta entre runs revela si el moat se está erosionando. Ese es el trigger para Phase 2.5.

**No peer discovery por SIC code**: los SIC codes de 4 dígitos son ruidosos — NVIDIA y un fabricante de componentes commodity pueden estar en el mismo grupo. Eduardo conoce sus peers mejor que cualquier heurística. Preguntar en Checkpoint 1 tiene además valor Socratic: fuerza articular "¿contra quién compite realmente esta empresa hoy?".

**No NewsAPI ni GDELT**: lo que faltaba no era más noticias (Tavily + Finnhub cubren el canal cualitativo), sino **event tracking primary-source**: los 8-K de la SEC para capturar restatements (Item 4.02), cambios de CEO (Item 5.02), y earnings (Item 2.02) directamente del regulador. Sin intermediarios.

---

## 9. La arquitectura como proceso de aprendizaje

El sistema tiene una dualidad intencional: es una herramienta de decisión de inversión y un entorno de aprendizaje sobre análisis fundamental.

Las preguntas Socratic generadas desde datos reales ("ROIC es 87% pero el moat es 'narrow' — ¿qué lo reconciliaría?") no son preguntas de un quiz genérico. Son preguntas que solo pueden formularse si el sistema ya leyó el filing y calculó los números. Eso transforma el checkpoint de un "¿continuar? Y/N" a un momento de reflexión genuina.

El CAGR histórico en Checkpoint 2 tiene el mismo propósito: antes de teclear "growth=12%", el sistema muestra que la empresa ha crecido al 3% los últimos 4 años y pregunta por qué asumes aceleración. No bloquea la decisión — obliga a articularla.

---

## 10. Arquitectura IA: dónde encaja este sistema y qué conceptos refuerza

### Los patrones de agentes más usados hoy

El campo de los "agentes LLM" tiene varios patrones arquitectónicos. Es útil saber cuál usa este sistema y por qué.

**ReAct (Reason + Act)**
El LLM decide en cada paso qué herramienta usar ("voy a buscar el 10-K", "ahora calcularé el DCF"). Es el patrón más flexible pero el menos predecible: el LLM puede decidir no buscar algo, buscar lo incorrecto, o entrar en loops. Lo usa GPT con function calling, Claude con tool use, Llama Index en modo agente.

*Este sistema NO usa ReAct.* Las herramientas se invocan de forma **programática y determinista** — el código decide cuándo llamar a EDGAR, Polygon y Finnhub, no el LLM. El LLM solo razona sobre los datos ya traídos.

**Plan-and-Execute**
Un agente "planificador" genera un plan de pasos, y un agente "ejecutor" los implementa uno a uno. Bueno para tareas abiertas. Problema: el plan puede ser incoherente y los errores en el paso 2 se propagan al paso 10.

**Supervisor-Worker (el patrón de este sistema)**
Un nodo supervisor orquesta el trabajo y despacha a especialistas. Los especialistas son independientes entre sí. El supervisor consolida. Es el patrón más robusto para pipelines donde cada paso tiene un dominio bien definido.

```
supervisor_node
    ├── management_agent   (EDGAR + Tavily → ManagementReport)
    ├── filings_agent      (EDGAR 10-K → FilingsRAGReport)
    ├── market_agent       (Polygon + Finnhub → MarketReport)
    └── competitive_agent  (ROIC + Tavily → CompetitiveReport)
            ↓
    value_expert_agent  (sintetiza todos los reports)
```

**Reflexion / Self-critique**
El agente evalúa su propio output y lo itera. El Critic de este sistema es una versión simplificada: un agente separado que lee el ValueExpertReport y genera contra-tesis. La diferencia con self-critique puro es que es un agente distinto (no el mismo LLM evaluándose), lo que reduce el sesgo de confirmación.

---

### LangGraph vs los otros frameworks

| Framework | Abstracción | Control | Cuándo usarlo |
|---|---|---|---|
| **LangGraph** | Grafo de estado explícito | Alto | Pipelines complejos, HITL, estado persistente, producción |
| **LangChain** | Chains y agents más simples | Medio | Prototipos rápidos, pipelines lineales |
| **CrewAI** | Roles y crews de alto nivel | Bajo | Equipos de agentes con lenguaje natural como orquestación |
| **AutoGen** | Actor model (mensajes entre agentes) | Medio | Conversaciones multi-agente, código generativo |
| **Llama Index** | Workflows orientados a RAG | Medio | RAG avanzado, indexación de documentos |
| **Pure Python** | Ninguna | Total | Cuando ningún framework encaja |

**Por qué LangGraph para este sistema:**
- El estado del análisis tiene ~20 campos (FinancialsReport, ManagementReport, etc.) — necesitas un TypedDict con merge automático.
- Los checkpoints requieren `interrupt()` con persistencia real del estado — no es un `input()` de Python.
- El fan-out paralelo de 4 especialistas con `Send()` no existe de forma nativa en LangChain.
- LangGraph es el framework de producción de Anthropic y tiene el mejor soporte para HITL.

---

### Conceptos clave del campo que este sistema ilustra

**Grounding**
"Grounding" es el proceso de anclar el output del LLM en datos verificables. En sistemas sin grounding, el LLM "flota" — genera texto plausible sin fuente. Este sistema tiene dos mecanismos de grounding:
1. *Numérico*: todos los números vienen de EDGAR XBRL (determinista).
2. *Cualitativo*: todas las afirmaciones vienen con Citation que incluye excerpt verbatim del documento fuente.

El grounding es el problema central de los LLMs en aplicaciones de alta confianza (legal, médico, financiero). La solución no es "pedir al LLM que no alucine" — es diseñar el sistema para que los datos nunca pasen por el LLM.

**Context window management**
Los LLMs tienen un límite de tokens que pueden procesar a la vez. Este sistema lo gestiona en múltiples capas:
- Transcripts: 2 quarters × (2000 chars prepared + 3000 chars Q&A) = ~10K chars máximo.
- Tavily: 3 resultados por query × 800 chars = ~10K chars máximo.
- Filings: Item 1 (business), Item 1A (risks), Item 7 (MDA) — no el 10-K completo.

Más contexto no siempre es mejor. Los LLMs tienen el problema del "lost in the middle": les cuesta dar igual peso a información que aparece en el medio de un contexto muy largo. La selección de qué incluir es una decisión arquitectónica tan importante como el prompt.

**Provider abstraction**
Este sistema accede a los LLMs a través de un wrapper propio (`due_diligence.llm.client`) que abstrae el proveedor. Hoy usa OpenAI como primario. Si mañana quieres cambiar a Gemini o volver a Claude, cambias la implementación del wrapper, no los 5 agentes que lo usan.

Este patrón — que en software se llama Dependency Inversion — es crítico en AI porque los proveedores cambian de API, de precios y de capacidades con frecuencia. Atarte a un SDK específico es deuda técnica cara.

**Deterministic layer + probabilistic layer**
Una de las lecciones más importantes del diseño de sistemas con LLMs: separa lo determinista de lo probabilístico.

```
Deterministic:   EDGAR XBRL → ROIC, revenue, FCF, deuda
                 Polygon → precio, market cap
                 Finnhub → transcripts, noticias
                 reglas de routing, cálculos DCF

Probabilistic:   management_agent, filings_agent, competitive_agent
                 value_expert, critic
```

Los errores en la capa determinista son reproducibles y corregibles. Los errores en la capa probabilística son estocásticos — el mismo input puede dar outputs ligeramente distintos. Al mantener las capas separadas, puedes testear unitariamente el 100% de la capa determinista (188 tests en este proyecto) sin mockear LLMs.

**Memory en agentes**
Los sistemas agentes tienen típicamente tres tipos de memoria:

| Tipo | Dónde vive | En este sistema |
|---|---|---|
| **In-context** | El prompt actual | `AnalysisState` — todos los reports del análisis en curso |
| **External (short-term)** | Base de datos, archivos | `data/analyses.db` (SQLite) + markdowns archivados |
| **External (long-term/semantic)** | Vector store | Phase 2.5 — ChromaDB para delta entre re-análisis del mismo ticker |

La mayoría de sistemas "agentes" que ves en demos solo tienen memoria in-context. Los sistemas de producción necesitan las tres capas.

---

### Comparativa con sistemas similares en producción

**Perplexity** — Busca en la web, cita fuentes, genera resumen. Un agente con grounding parcial (cita URLs pero no verifica excerpts). Sin HITL, sin persistencia de estado. Modelo: single-pass.

**OpenAI Assistants API** — Estado persistente, file search, tool calling. Similar en concepto al grafo de este sistema pero cloud-hosted y con menos control sobre el flow. No tiene fan-out paralelo nativo ni interrupt() como LangGraph.

**Cursor / GitHub Copilot** — Agentes de código que usan el repositorio como contexto. Patrón similar al de este sistema: contexto determinista (código fuente) + razonamiento LLM. La diferencia es que el dominio es código, no finanzas.

**Sistemas de análisis financiero institucional** (Bloomberg GPT, FinGPT) — Modelos fine-tuneados en datos financieros. Diferente approach: en lugar de arquitectura con herramientas, entrenan el modelo para que "sepa" finanzas. Problema: el fine-tuning sobre datos históricos no garantiza que los datos del análisis en curso sean correctos ni actuales.

**La decisión clave de este sistema** frente a todos los anteriores: no confiar en que el LLM "sepa" los números — traerlos siempre de la fuente. Esto lo hace más robusto pero más complejo de construir.

---

### El estado del arte: hacia dónde va el campo

**Reasoning models** (o1, o3, Deepseek-R1): modelos que hacen chain-of-thought interno antes de responder. Para este sistema, son candidatos interesantes para el Value Expert (la síntesis es exactamente el tipo de razonamiento complejo donde brillan). El tradeoff es latencia y coste.

**Agentic loops con evaluación automática**: sistemas donde el agente evalúa su propio output y lo itera automáticamente. El Critic de este sistema es una versión manual de esto. La versión automática (LLM juzga si el output es suficientemente bueno y lo rehace) requiere cuidado — puede producir loops infinitos o convergencia prematura.

**Computer use y multimodal**: agentes que navegan interfaces gráficas o procesan PDFs escaneados. Relevante para este sistema en Phase 3 (EU filings que no son XBRL sino PDFs en varios idiomas).

**MCP (Model Context Protocol)**: protocolo de Anthropic para exponer herramientas y recursos a los LLMs de forma estandarizada. Este sistema podría exponerse como un MCP server en Phase 5 — el análisis de una empresa como un tool que cualquier LLM puede invocar.

---

## En resumen

| Concepto | Implementación | Por qué |
|---|---|---|
| Multi-agente paralelo | LangGraph fan-out con Send() | Cada especialista tiene un prompt optimizado; paralelismo real |
| Zero hallucination numérica | Tool-lane agents + EDGAR XBRL | Los números nunca pasan por el LLM |
| Trazabilidad de claims | Citations con excerpt + substring-match | Cada afirmación cualitativa apunta a su fuente primaria |
| HITL Socratic | interrupt() + heurísticas de datos | Fuerza articulación consciente, no solo aprobación |
| Síntesis de calidad | Value Expert prompt v2: adjudicar, no resumir | El Expert integra conflictos; §6 ancla en historia; §9 nombra thesis-breaker |
| Complejidad diferida | ADRs de deferral (RAG, SqliteSaver, peer discovery) | Solo construir lo que tiene ROI demostrable hoy |
