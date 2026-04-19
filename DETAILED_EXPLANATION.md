# DETAILED_EXPLANATION.md

Documentación didáctica acumulativa. Se actualiza al finalizar cada tarea, fase o milestone.
Objetivo: que Eduardo pueda entender qué se construyó, por qué, y qué conceptos de programación/IA están detrás.

---

## Phase 0 — Scaffolding

### Task 1: Repo skeleton (`pyproject.toml`, `.gitignore`, `.env.example`, `uv sync`)

#### ¿Qué se hizo?
Se creó la estructura base del proyecto: archivos de configuración, manifiesto de dependencias, y el esqueleto de carpetas.

#### `pyproject.toml` — el manifiesto del proyecto
Es el equivalente moderno de `setup.py` o `requirements.txt`. Define:
- Nombre y versión del paquete.
- Dependencias de producción (`dependencies`) y de desarrollo (`[project.optional-dependencies] dev`).
- El entry point del CLI: `due-diligence = "due_diligence.cli:app"` — esto registra el comando `uv run due-diligence` en el PATH del entorno virtual.
- `[build-system]` con `hatchling`: el build backend que empaqueta el código para distribución.

#### `uv` — por qué no `pip`
`uv` es un gestor de paquetes escrito en Rust, 10-100x más rápido que pip. Resuelve dependencias con un SAT solver (garantiza versiones compatibles), crea el lockfile `uv.lock`, y aísla el entorno en `.venv/`. El comando `uv sync --extra dev` instala tanto dependencias de producción como de desarrollo.

#### `src layout` — por qué el código vive en `src/due_diligence/` y no en `due_diligence/`
Sin el `src layout`, cuando ejecutas `pytest` desde la raíz, Python puede importar el directorio `due_diligence/` directamente (sin instalarlo), lo que significa que el código que se testea no es exactamente el mismo que se instalará. Con el `src layout`, el paquete solo es importable si está instalado (`uv sync`), lo que garantiza paridad entre tests y producción.

#### `.gitignore`
Excluye archivos que nunca deben entrar al repositorio: `.env` (contiene secretos), `.venv/` (entorno virtual reproducible desde `pyproject.toml`), `data/` (análisis generados en runtime), `__pycache__/` (bytecode generado por Python).

#### `.env.example`
Plantilla de variables de entorno requeridas. El `.env` real (con claves verdaderas) nunca se commitea; `.env.example` documenta qué variables existen sin exponer valores.

---

### Task 2: MCE gate — subdirectorios `CLAUDE.md` (MCE-01, 02, 04, 05)

#### ¿Qué se hizo?
Se crearon 4 archivos `CLAUDE.md` en los subdirectorios que se acababan de crear.

#### ¿Qué es un subdirectorio `CLAUDE.md`?
Claude Code lee automáticamente cualquier `CLAUDE.md` que encuentre en el árbol de directorios del proyecto. Cada uno es contexto "always-on" para ese subdirectorio: convenciones de código, invariantes, qué NO hacer. Es el equivalente a un `README` para el asistente de IA, no para el humano.

#### ¿Por qué se crean ahora y no antes?
Por el patrón MCE de "deferred triggers" acordado en el diseño: no creamos estos archivos hasta que los directorios existen. Un `CLAUDE.md` para un directorio que no existe es ruido que envejece mal.

#### ¿Qué contiene cada uno?
- `src/due_diligence/CLAUDE.md` (MCE-01): estructura del módulo, invariantes de código (Pydantic obligatorio, no importar anthropic/openai directo, structlog para logging).
- `src/due_diligence/llm/CLAUDE.md` (MCE-04): reglas del provider wrapper (solo este módulo importa anthropic/openai, fallback policy, prompt caching).
- `src/due_diligence/tools/CLAUDE.md` (MCE-02): patrón fetch_raw/parse, fixture-first, fail-loud.
- `tests/CLAUDE.md` (MCE-05): zero API calls reales en tests, fixtures JSON, pytest-mock, naming.

---

### Task 3: Config module (`config.py` con pydantic-settings)

#### ¿Qué se hizo?
Se creó `src/due_diligence/config.py` con un objeto `settings` que lee variables de entorno y las valida.

#### `pydantic-settings` — qué es y por qué
`pydantic-settings` extiende Pydantic para leer configuración desde variables de entorno (o un archivo `.env`). El resultado es un objeto Python tipado. En lugar de esto:

```python
import os
api_key = os.getenv("ANTHROPIC_API_KEY")  # tipo: str | None, sin validación
```

Tenemos esto:
```python
from due_diligence.config import settings
settings.anthropic_api_key  # tipo: str, validado, obligatorio
```

Si `ANTHROPIC_API_KEY` no está definida, el programa falla al arrancar con un error claro de Pydantic — no en producción cuando intenta hacer la primera llamada.

#### `validation_alias` — cómo se mapean env vars a campos
```python
anthropic_api_key: str = Field(..., validation_alias="ANTHROPIC_API_KEY")
```
El campo Python usa snake_case (`anthropic_api_key`). La env var usa UPPER_CASE (`ANTHROPIC_API_KEY`). `validation_alias` hace el mapeo.

#### `settings = Settings()` a nivel de módulo
Al hacer `from due_diligence.config import settings`, Python ejecuta `Settings()` una sola vez al importar el módulo. Todas las partes del sistema comparten la misma instancia. Es un **module-level singleton** — no es el patrón Singleton explícito, simplemente aprovecha el cacheo de módulos de Python.

#### Tests con `monkeypatch` — ¿qué es?
`monkeypatch` es un fixture de pytest que permite modificar el entorno de un test sin afectar otros tests. `monkeypatch.setenv("KEY", "value")` setea una variable de entorno solo durante ese test; al terminar, la revierte automáticamente. Útil para simular distintas configuraciones sin tocar archivos reales.

El truco `importlib.reload(cfg_module)` fuerza que Python re-evalúe el módulo (re-ejecuta `settings = Settings()` con las nuevas env vars). Sin el reload, Python usaría la instancia cacheada del import anterior.

---

### Task 4: Pydantic schemas (`Citation`, `Claim`, `MetricValue`, `FinancialsReport`)

#### ¿Qué se hizo?
Se definieron los modelos de datos fundamentales del sistema en `src/due_diligence/schemas/base.py`.

#### ¿Por qué Pydantic y no dataclasses o dicts?
Pydantic valida los datos al instanciar el objeto. Si intentas crear un `Citation` con `confidence=1.5`, falla inmediatamente con un error descriptivo. Con un `dict` o `dataclass`, ese error aparecería mucho más tarde (o nunca), propagándose silenciosamente. En un sistema donde "zero hallucination numérica" es una invariante arquitectónica, la validación en el punto de creación es crítica.

#### `Citation` — el modelo anti-hallucination cualitativo
```python
class Citation(BaseModel):
    claim: str          # el claim que hace el LLM
    source_url: str     # URL del filing donde verificarlo
    excerpt: str        # texto exacto del filing que lo soporta
    confidence: float = Field(..., ge=0.0, le=1.0)  # 0 = no verificado
    gaap_vs_nongaap_flag: Optional[str] = None
```
`ge=0.0, le=1.0` son constraints de Pydantic: `ge` = greater or equal, `le` = less or equal. Si `confidence > 1.0`, Pydantic lanza `ValidationError` antes de que el dato entre al sistema.

Un claim con `confidence=0.0` significa "no se pudo verificar por substring-match contra el filing" — se incluye en `unverified_claims` del report pero se excluye de la síntesis del Value Expert.

#### `MetricValue` — envuelve cada número financiero
```python
class MetricValue(BaseModel):
    value: float
    unit: str = "USD"
    period: str        # "FY2024", "TTM", "Q3-2024"
    source: str        # "XBRL", "calculated", "10-K"
    currency_code: str = "USD"
    gaap_vs_nongaap: str = "GAAP"
```
Nunca un `float` suelto. Siempre con metadatos. Esto permite que el Value Expert diga "Revenue FY2024: $391B (GAAP, XBRL, USD)" en lugar de solo "$391000000000", y permite auditar cualquier número hasta su fuente.

#### `FinancialsReport` — output del Edgar adapter
Las métricas de income statement son `list[MetricValue]` (multi-año) para poder calcular tendencias: "ROIC ha sido >15% los últimos 5 años". El índice 0 siempre es el año más reciente.

---

### Task 5: LLM client (`src/due_diligence/llm/client.py`)

#### ¿Qué se hizo?
Se creó un wrapper thin sobre los SDKs de Anthropic y OpenAI con fallback automático.

#### ¿Por qué un wrapper y no importar `anthropic` directamente?
Provider abstraction (ADR-004). Si Claude sube de precio, se cae, o cambia su API, solo hay que modificar `client.py`. El resto del sistema no sabe qué proveedor está detrás.

#### Lazy imports — `import anthropic` dentro del método
```python
def _get_anthropic(self):
    if self._anthropic is None:
        import anthropic
        self._anthropic = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return self._anthropic
```
El import está dentro del método, no en el top del archivo. Motivo: si `anthropic` no está instalado o la API key no está configurada, el error ocurre solo cuando se intenta usar, no al importar el módulo. También facilita los tests — podemos `patch("anthropic.Anthropic")` porque el import ocurre en el momento en que el test está activo.

#### `get_client()` — el singleton explícito
```python
_client: Optional[LLMClient] = None

def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
```
Un singleton garantiza que todas las partes del sistema comparten la misma instancia del cliente, con el mismo estado interno (el cliente de Anthropic cacheado). Sin esto, cada agente crearía su propio cliente, multiplicando conexiones HTTP.

#### Tests con `patch` — ¿cómo funciona el mocking?
```python
with patch("anthropic.Anthropic") as mock_anthropic_cls:
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    mock_anthropic_cls.return_value = mock_client
    ...
```
`patch("anthropic.Anthropic")` intercepta la clase `Anthropic` del módulo `anthropic` durante la duración del `with`. Cuando el código hace `anthropic.Anthropic(api_key=...)`, recibe el `MagicMock` en lugar de la clase real. `MagicMock()` es un objeto que acepta cualquier atributo y llamada de método, devolviendo más mocks — por eso `mock_client.messages.create.return_value = mock_response` funciona sin definir la estructura real.

El test de fallback usa `side_effect = Exception("rate limit")`: en lugar de devolver un valor, el mock lanza una excepción cuando se llama. Esto simula el fallo de Anthropic sin hacer ninguna llamada real.

---

### Task 6: Edgar adapter (`src/due_diligence/tools/edgar.py`)

#### ¿Qué se hizo?
Se implementó el adaptador a SEC EDGAR en dos capas:
- `EdgarAdapter.fetch_raw(ticker)` — llamada de red real a EDGAR vía edgartools.
- `EdgarAdapter.parse_financials(raw)` — función pura que transforma el dict en `FinancialsReport`.

#### La separación fetch / parse — por qué importa
Si `fetch_raw` y `parse_financials` estuvieran en el mismo método, sería imposible testear el parsing sin hacer llamadas reales a EDGAR. Al separarlos:
- Los **unit tests** llaman solo a `parse_financials(fixture)` — 0 red, 0 dependencias externas.
- Los **integration tests** (marcados `@pytest.mark.integration`) llaman a `fetch_raw` con red real.
- Puedes debuggear el parsing guardando el `raw` y re-ejecutando localmente.

#### `DataUnavailableError` — fail loud
Si un concepto XBRL no existe en el filing de una empresa, el adapter lanza `DataUnavailableError` explícitamente. Nunca devuelve `None` silenciosamente para una métrica requerida. Esto cumple ADR-005: "tools fallan loud".

#### Free Cash Flow como `source="calculated"`
FCF = OCF - Capex. No existe como concepto XBRL en los filings. Lo calculamos nosotros y lo marcamos con `source="calculated"` para que siempre sea trazable: "este número lo computamos nosotros, no lo reportó la empresa directamente".

#### Cash = cash + short-term investments
AAPL, como muchas tech companies, tiene gran parte de su "cash" en short-term investments (bonos del Tesoro a corto plazo). Si solo sumamos `CashAndCashEquivalentsAtCarryingValue`, subestimamos el cash disponible. La convención en value investing es sumar ambos para calcular el "net cash" real.

#### `tests/fixtures/aapl_xbrl_raw.json` — el fixture
Un JSON con valores reales de AAPL FY2024 (del 10-K de septiembre 2024). Se usa en los tests para no hacer llamadas reales. Los valores son auditables contra el filing público de SEC.

#### `tests/conftest.py` — el problema que resolvió
`config.py` tiene `settings = Settings()` a nivel de módulo. Sin variables de entorno definidas, falla al importar. `conftest.py` con `autouse=True, scope="session"` setea las vars mínimas antes de que cualquier test importe código del proyecto. El `setdefault` (no `setenv`) asegura que si el usuario tiene un `.env` real, no lo sobreescribimos.

---

### Task 7: CLI (`src/due_diligence/cli.py`) y smoke test

#### ¿Qué se hizo?
Se creó el entry point del CLI con `typer` y el test de integración más ligero.

#### `typer` — qué es y por qué no `argparse`
`typer` genera CLIs a partir de type hints de Python. La función:
```python
@app.command("get-financials")
def get_financials_cmd(ticker: str = typer.Argument(...)):
```
...genera automáticamente `due-diligence get-financials AAPL` con validación de argumentos, help text, y autocompletado. `argparse` requiere definir todo manualmente. `typer` es YAGNI para CLIs.

#### `rich` — output presentable sin esfuerzo
`rich.Table` genera tablas con colores, alineación y bordes en el terminal. El CLI imprime los ratios financieros en una tabla donde la columna `Source` siempre muestra "XBRL" o "calculated" — visualmente verifica la invariante de zero hallucination.

#### El smoke test — qué valida exactamente
```python
def test_smoke_financials_pipeline_from_fixture():
    fixture → EdgarAdapter.parse_financials → FinancialsReport
```
No testea ningún componente en aislamiento — testea que **todos los componentes conectan entre sí**: el fixture JSON se puede leer, el adapter lo transforma, el schema lo valida. Si cualquier refactor rompe la integración, este test falla.

---

### Lección aprendida de Phase 0

Esta fase se implementó sin pausas didácticas, violando la regla "Modo Profesor". Todos los conceptos documentados aquí se deberían haber explicado *antes* de codificar cada componente, con tiempo para preguntas. A partir de Phase 1, el flujo es:

1. Explicar el componente antes de implementarlo.
2. Parar y preguntar si hay dudas.
3. Implementar una tarea.
4. Actualizar este documento.
5. Parar y preguntar antes de continuar.

---

---

## Phase 1a — LangGraph Skeleton + Financials Specialist

### Contexto: ¿qué es Phase 1a y qué objetivo tiene?

Phase 1 se dividió en tres milestones para no abarcar demasiado de golpe:
- **1a** (actual): montar el esqueleto del grafo con estado, nodos básicos, y el especialista de Financials.
- **1b**: añadir los 4 especialistas restantes (Management, Filings, Market, Competitive) con fan-out paralelo y HITL.
- **1c**: agentes finales (Valuation, Value Expert, Critic) y archivador.

Al terminar Phase 1a, el comando `due-diligence analyze AAPL` debería correr de extremo a extremo y devolver datos reales de EDGAR en un estado LangGraph validado.

---

### Task 1: Stub report schemas (`src/due_diligence/schemas/reports.py`)

#### ¿Qué se hizo?
Se definieron los modelos Pydantic para los outputs de todos los agentes, aunque la mayoría son **stubs** — modelos con `stub=True` y todos los campos opcionales.

#### ¿Por qué crear schemas de agentes que no existen todavía?

`AnalysisState` (Task 3) necesita tipar todos los campos que el grafo puede contener. Si escribimos `financials: NotRequired[FinancialsReport]`, Python necesita que `FinancialsReport` ya exista en tiempo de importación. Lo mismo para `ManagementReport`, `MarketReport`, etc.

El truco es definirlos como **stubs**: modelos válidos que se pueden instanciar pero con todos los campos opcionales y un flag `stub=True`. El agente que los implemente de verdad hará dos cosas: rellenar los campos y cambiar `stub=False`.

```python
class ManagementReport(BaseModel):
    ticker: str
    stub: bool = True          # <- marca que este output es un placeholder
    insider_ownership_pct: Optional[float] = None  # se rellenará en Phase 1b
    # ...
```

Este patrón permite que el sistema compile y los tests pasen aunque los agentes no estén implementados. Es la diferencia entre "el grafo existe y corre" (Phase 1a) y "el grafo produce análisis reales" (Phase 1b/1c).

#### `CheckpointInput` y `ValuationInput` — los modelos de HITL

```python
class CheckpointInput(BaseModel):
    checkpoint_id: str    # "checkpoint_1" o "checkpoint_2"
    user_notes: str = ""  # observaciones libres del usuario
    proceed: bool = True  # False = abortar el análisis

class ValuationInput(CheckpointInput):
    wacc_pct: float = 9.0              # coste de capital
    terminal_growth_pct: float = 2.5  # crecimiento terminal
    projection_years: int = 10
```

HITL = Human In The Loop. En Phase 1b, el grafo se pausará dos veces usando `interrupt()` de LangGraph para esperar que el usuario revise los datos y aporte sus hipótesis de valoración. `CheckpointInput` / `ValuationInput` serializan esa input de forma tipada — no un dict libre. Están diseñados para funcionar en CLI ahora y en webapp más adelante.

---

### Task 2: MCE gate — directorios `agents/` y `graph/` con sus `CLAUDE.md`

#### ¿Qué se hizo?
Se crearon los directorios `src/due_diligence/graph/` y `src/due_diligence/agents/` con sus `__init__.py` y sus `CLAUDE.md`. Se resolvió MCE-03 de `mce-evolution.md`.

#### Recordatorio: ¿qué es el patrón MCE?

`docs/mce-evolution.md` es un registro de trabajo estructural diferido con **triggers verificables**. En lugar de crear toda la estructura de directorios al inicio (cuando no sabemos qué necesitaremos), definimos "cuando exista X, crear Y". Al arrancar cada fase o crear un directorio nuevo, revisamos la lista para ejecutar los items cuyo trigger se cumplió.

MCE-03 decía: "cuando se cree `src/due_diligence/agents/`, crear su `CLAUDE.md`". Al crear el directorio en Phase 1a, se cumplió el trigger y se creó el archivo.

#### ¿Qué contiene `graph/CLAUDE.md`?
Las reglas del módulo `graph/`:
- `state.py` solo define `AnalysisState` — nada más.
- `graph.py` solo define `build_graph()` — importa nodos de `agents/`, no implementa lógica.
- `build_graph(checkpointer=None)` acepta checkpointer opcional — los tests pasan `MemorySaver()` (sin SQLite), producción pasa `SqliteSaver`.

#### ¿Qué contiene `agents/CLAUDE.md`?
Las reglas de los nodos:
- Cada agente es una función pura: `def agent_node(state) -> dict`.
- Devuelve **solo las claves que modifica**, nunca el estado completo.
- Agentes "tool-lane" (Financials, Market): llaman adapters, sin LLM.
- Agentes "LLM-lane" (Management, Filings, Competitive): usan `get_client()`, emiten Citations.

---

### Task 3: `AnalysisState` — la pizarra del grafo

#### ¿Qué se hizo?
Se creó `src/due_diligence/graph/state.py` con la definición del estado compartido de todo el grafo LangGraph.

#### ¿Qué es un TypedDict?

Un TypedDict es una forma de decirle a Python: "un dict con estas claves específicas y estos tipos". A diferencia de Pydantic, **no valida en runtime** — es solo un contrato de tipos que herramientas como mypy/pyright verifican en análisis estático. LangGraph lo usa porque necesita poder serializar/deserializar el estado a JSON (para el checkpointer de SQLite), y los dicts son trivialmente serializables.

```python
class AnalysisState(TypedDict):
    ticker: str                       # obligatorio
    cik: NotRequired[str]             # opcional — lo escribe el Supervisor
    financials: NotRequired[FinancialsReport]  # lo escribe el Financials agent
    # ...
```

#### `NotRequired` — ¿qué problema resuelve?

Sin `NotRequired`, Python esperaría que todos los campos estuvieran en el dict al crearlo. Pero al invocar el grafo, solo tenemos el ticker:

```python
graph.invoke({"ticker": "AAPL"})  # <- no tenemos nada más en este punto
```

Los demás campos aparecen a medida que los nodos corren. `NotRequired` indica exactamente eso: "este campo puede no estar presente en el dict". Es una feature de Python 3.11+ para TypedDict.

#### ¿Por qué `as_of_date` es `str` y no `datetime.date`?

El checkpointer de SQLite serializa el estado a JSON. `datetime.date` no es JSON-serializable por defecto, pero un string `"2024-09-28"` sí. El nodo que lo use hace `date.fromisoformat(state["as_of_date"])` para convertir.

#### ¿Por qué `checkpoint_user_input` es `dict` y no `CheckpointInput`?

Si `state.py` importara `CheckpointInput` de `schemas/reports.py`, y `reports.py` a su vez necesitara importar algo de `state.py` en el futuro, tendríamos una importación circular. Usar `dict` aquí es un compromiso deliberado: el nodo que consuma el input hace la conversión `CheckpointInput(**state["checkpoint1_user_input"])`.

#### El flujo de datos a través del estado

```
START
  │  {"ticker": "AAPL"}
  ▼
supervisor_node
  │  añade: cik, as_of_date, run_id
  ▼
financials_node
  │  añade: financials (FinancialsReport con datos XBRL reales)
  ▼
[Phase 1b: más nodos en paralelo via Send()]
[Phase 1c: valuation, synthesis, critique]
  ▼
END  ←  estado completo con todos los campos
```

Cada nodo recibe el estado completo y devuelve un dict con solo las claves que modificó. LangGraph hace el merge (actualiza el estado con el dict parcial).

---

### Regla nueva: documentación del código

En esta sesión se añadió una regla a `CLAUDE.md`: todo archivo Python generado debe incluir:
1. **Docstring de archivo** — qué hace el módulo, por qué existe, cómo encaja en la arquitectura.
2. **Docstrings de funciones y clases** — conciso: qué hace, qué recibe, qué devuelve.
3. **Comentarios inline didácticos** — cuando el WHY no es obvio.

A partir de Task 3, todos los archivos generados siguen esta regla. Los archivos de Phase 0 (config.py, edgar.py, client.py) se documentarán cuando se modifiquen.

---

---

### Task 4: Esqueleto del grafo (`src/due_diligence/graph/graph.py`)

#### ¿Qué se hizo?
Se creó `graph.py` con `build_graph()`, y stubs mínimos de `supervisor_node` y `financials_node` para que el grafo compile. Las implementaciones reales llegan en Tasks 5 y 6.

#### `StateGraph` — cómo LangGraph construye un grafo

```python
builder = StateGraph(AnalysisState)   # le dice qué forma tiene el estado
builder.add_node("supervisor", supervisor_node)
builder.add_node("financials", financials_node)
builder.add_edge(START, "supervisor")
builder.add_edge("supervisor", "financials")
builder.add_edge("financials", END)
graph = builder.compile(checkpointer=checkpointer)
```

El proceso es declarativo: primero describes la estructura, luego `compile()` valida que todos los nodos son alcanzables, que no hay ciclos inesperados, y devuelve el `CompiledGraph` ejecutable.

#### ¿Por qué `build_graph()` es una factory function?

Si el grafo fuera un objeto global al nivel del módulo, estaría compilado con un checkpointer fijo. Pero tests y producción necesitan checkpointers distintos:

| Contexto | Checkpointer | Por qué |
|---|---|---|
| Tests | `MemorySaver()` | Sin SQLite, estado en RAM, se limpia automáticamente |
| Producción | `SqliteSaver(conn)` | Persiste el estado entre runs, permite resumir análisis |
| Sin persistencia | `None` | Grafo stateless, útil para pruebas rápidas |

Con la factory, cada llamada crea un grafo fresco con el checkpointer deseado.

#### Phase 1a vs Phase 1b — la diferencia en el grafo

Phase 1a es lineal: `supervisor → financials`. Phase 1b añadirá fan-out con `Send()`:

```python
# Phase 1b: el supervisor lanzará todos los especialistas en paralelo
def route_to_specialists(state):
    return [
        Send("mgmt", state),
        Send("filings", state),
        Send("market", state),
        Send("competitive", state),
        Send("financials", state),
    ]

builder.add_conditional_edges("supervisor", route_to_specialists)
```

`Send()` clona el estado y lo envía a varios nodos simultáneamente. LangGraph los ejecuta en paralelo y hace el merge de los resultados automáticamente.

#### Los stubs de `supervisor.py` y `financials.py`

Creados solo para que `graph.py` pueda importarlos. Devuelven valores hardcodeados:
- `supervisor_node` devuelve `{"cik": "STUB", ...}`
- `financials_node` devuelve `{"financials": None}`

Se reemplazarán con lógica real en Tasks 5 y 6. El patrón de "stub primero, implementar después" permite construir el grafo antes de tener todos los agentes, y verificar que la estructura es correcta.

---

---

### Task 5: Supervisor node (`src/due_diligence/agents/supervisor.py`)

#### ¿Qué se hizo?
Se implementó el primer nodo real del grafo. Recibe `{"ticker": "AAPL"}` y escribe `cik`, `as_of_date`, `run_id` en el estado antes de que los especialistas corran.

#### Ticker vs CIK — ¿por qué resolver esto aquí?

El ticker ("AAPL") es el nombre que usa el mercado. La SEC usa internamente el CIK ("Central Index Key") — un entero único por empresa, ej: `320193` para Apple. EDGAR no busca por ticker, busca por CIK. El Supervisor hace esta resolución una sola vez para todo el análisis:

```python
company = Company(ticker)          # edgartools: busca en el índice de la SEC
cik = str(company.cik).zfill(10)   # 320193 → "0000320193" (formato estándar de 10 dígitos)
latest_10k = company.get_filings(form="10-K").latest()
as_of_date = str(latest_10k.period_of_report)  # "2024-09-28"
```

`zfill(10)` rellena con ceros a la izquierda para cumplir el formato estándar de la SEC.

#### `as_of_date` como string ISO

Ya explicado en Task 3: el checkpointer de SQLite serializa el estado a JSON. `datetime.date` no es JSON-serializable; el string `"2024-09-28"` sí. Los nodos que necesiten un objeto `date` hacen `date.fromisoformat(state["as_of_date"])`.

#### `run_id` — identificador del análisis

UUID truncado a 12 caracteres. Es el `thread_id` que el checkpointer de SQLite usa para separar los estados de distintos análisis en la misma base de datos. Sin `thread_id`, todos los análisis compartirían el mismo estado y se mezclarían.

#### Campos cross-cutting inicializados aquí

```python
return {
    ...
    "citations": [],
    "unverified_claims": [],
    "cost_usd": 0.0,
}
```

El Supervisor inicializa estas listas vacías para que los agentes LLM downstream puedan hacer `state["citations"].append(...)` sin comprobar si la lista existe. Solo se inicializan una vez, aquí.

#### Lección de mocking: imports top-level vs lazy

En Task 5 descubrimos una regla importante de `pytest-mock`:

`mocker.patch("modulo.Nombre")` solo funciona si `Nombre` es un atributo del módulo en el momento del patch. Con lazy imports dentro de la función (`from edgar import Company` dentro del `def`), `Company` no existe en el namespace del módulo y el patch falla con `AttributeError`.

La solución es siempre importar a nivel de módulo lo que necesites mockear en tests. En este proyecto, `edgar` siempre está disponible (está en `uv.lock`), así que top-level import es la opción correcta.

---

### Task 6: Financials specialist node (`src/due_diligence/agents/financials.py`)

#### ¿Qué se hizo?
Se reemplazó el stub con la implementación real. El nodo llama a `EdgarAdapter` (de Phase 0) y devuelve un `FinancialsReport` con datos XBRL reales.

#### La implementación es mínima — ¿por qué?

```python
def financials_node(state: AnalysisState) -> dict:
    ticker = state["ticker"]
    raw = EdgarAdapter.fetch_raw(ticker)
    report = EdgarAdapter.parse_financials(raw)
    return {"financials": report}
```

Solo 4 líneas de lógica. Toda la complejidad (XBRL parsing, MetricValue, FCF calculado) ya vive en `EdgarAdapter` de Phase 0. El nodo es un wrapper thin que:
1. Lee del estado.
2. Llama al adapter.
3. Devuelve solo la clave que produce.

Este es el patrón correcto para nodos tool-lane: delegan en el adapter, no reimplementan lógica.

#### ¿Por qué devolver solo `{"financials": report}`?

LangGraph hace el merge automáticamente: `new_state = {**old_state, **node_result}`. Si el nodo devolviera el estado completo, sobreescribiría las claves que otros nodos escribieron. Al devolver solo la clave propia, cada nodo es responsable únicamente de su output. Esto es lo que el test `test_financials_node_only_returns_financials_key` verifica explícitamente.

---

---

### Task 7: Test end-to-end + comando `analyze`

#### ¿Qué se hizo?
Se extendió `test_graph_1a.py` con dos tests que invocan el grafo completo, y se añadió el comando `due-diligence analyze AAPL` al CLI.

#### El test end-to-end — qué hace diferente a los tests unitarios

Los tests de Tasks 5 y 6 prueban cada nodo en aislamiento. El test end-to-end prueba que los nodos **se conectan correctamente**:

```python
graph = build_graph(checkpointer=MemorySaver())
config = {"configurable": {"thread_id": "test-run-1a"}}
result = graph.invoke({"ticker": "AAPL"}, config=config)

assert result["cik"] == "0000320193"           # lo escribió supervisor_node
assert isinstance(result["financials"], ...)   # lo escribió financials_node
assert result["financials"].revenue[0].value == pytest.approx(391_035_000_000)
```

`graph.invoke()` corre el grafo de extremo a extremo: `START → supervisor → financials → END`. El estado final es el merge de lo que cada nodo devolvió. Si los nodos devolvieran el estado completo en lugar de solo sus claves, este test lo detectaría porque los datos se sobreescribirían.

#### `thread_id` — por qué es obligatorio con checkpointer

Cuando el grafo tiene un checkpointer (MemorySaver o SqliteSaver), LangGraph necesita un `thread_id` para saber en qué "slot" guardar el estado. Sin `thread_id`, LangGraph lanza un error. Cada test usa un `thread_id` distinto para no contaminar el estado de otros tests.

```python
config = {"configurable": {"thread_id": "test-run-1a"}}
result = graph.invoke({"ticker": "AAPL"}, config=config)
```

En producción, `thread_id = f"{ticker}-latest"` — permite re-invocar el mismo análisis y reanudar desde donde se quedó si se interrumpió.

#### El comando `analyze` — diferencias con `get-financials`

`get-financials` (Phase 0) llama directamente al adapter: `EdgarAdapter.fetch_raw()`. Es un comando de debug.

`analyze` (Phase 1a) corre el grafo LangGraph completo con SqliteSaver. La diferencia clave:

| | `get-financials` | `analyze` |
|---|---|---|
| Llama a | EdgarAdapter directamente | Grafo LangGraph |
| Persistencia | Ninguna | SQLite (`data/analyses.db`) |
| Checkpointer | No | SqliteSaver |
| Expandible | No (hardcoded) | Sí — añadir nodos en Phase 1b/1c |

#### `SqliteSaver` vs `MemorySaver`

- `MemorySaver`: estado en RAM. Se pierde al cerrar el proceso. Perfecto para tests.
- `SqliteSaver`: estado en `data/analyses.db`. Persiste entre ejecuciones del CLI. Permite reanudar análisis interrumpidos y, en Phase 2, comparar análisis históricos.

---

### Phase 1a — Visión de conjunto

Con los 7 tasks completados, Phase 1a está terminada. Lo que se construyó:

```
src/due_diligence/
├── graph/
│   ├── state.py       ← AnalysisState TypedDict (la pizarra)
│   └── graph.py       ← build_graph() factory
├── agents/
│   ├── supervisor.py  ← ticker→CIK, as_of_date, run_id
│   └── financials.py  ← EdgarAdapter wrapper, tool-lane
└── schemas/
    └── reports.py     ← Stubs Pydantic para agentes futuros
```

Tests: 32 passing, 0 real API calls.

El comando `uv run due-diligence analyze AAPL` ahora corre el grafo completo contra EDGAR real y muestra una tabla de financials.

Lo que Phase 1b añadirá: Management, Filings, Market, Competitive specialists en paralelo vía `Send()`, más los dos checkpoints HITL donde el usuario podrá revisar datos y aportar hipótesis de valoración.

---

---

### Fixes de integración — del código al live run

Al ejecutar `uv run due-diligence analyze AAPL` por primera vez aparecieron 4 problemas reales. Documentados aquí porque cada uno enseña algo relevante sobre el stack.

---

#### Fix 1: `langgraph-checkpoint-sqlite` — paquete separado

**Error:** `ModuleNotFoundError: No module named 'langgraph.checkpoint.sqlite'`

**Causa:** A partir de LangGraph 1.x, el checkpointer de SQLite se separó del paquete core y vive en `langgraph-checkpoint-sqlite`. El import `from langgraph.checkpoint.sqlite import SqliteSaver` sigue siendo el mismo, pero hay que instalar el paquete adicional.

**Solución:** añadir `langgraph-checkpoint-sqlite>=2.0.0` a `pyproject.toml` y `uv sync`.

**Lección:** las librerías grandes tienden a descomponer funcionalidades opcionales en paquetes separados para reducir el tamaño del install base. Al actualizar una librería, vale la pena revisar el changelog por si algún módulo se movió a un paquete propio.

---

#### Fix 2: `ANTHROPIC_API_KEY` opcional — OpenAI como proveedor principal

**Problema:** `config.py` tenía `ANTHROPIC_API_KEY: str = Field(...)` — el `...` en Pydantic significa "obligatorio". Sin la key, el sistema no arrancaba aunque no se fuera a usar Claude.

**Solución:** cambiar a `Field("")` (string vacío por defecto) y añadir lógica en `LLMClient.complete()`:

```python
if not settings.anthropic_api_key:
    return self._complete_openai(messages, system, max_tokens)
```

Si la key está vacía, va directo a OpenAI sin intentar Anthropic. Esto cumple el espíritu de ADR-004 (provider-agnostic): el sistema funciona con cualquier proveedor disponible.

**Lección sobre `monkeypatch` vs `.env`:** al actualizar los tests descubrimos que `monkeypatch.delenv("KEY")` elimina la variable del entorno del proceso, pero `pydantic-settings` también lee el archivo `.env` del disco. El env var tiene prioridad sobre el archivo, así que `monkeypatch.setenv("KEY", "")` (valor vacío) sí sobreescribe el `.env`. `delenv` no es suficiente cuando el `.env` proporciona un fallback.

---

#### Fix 3: edgartools 5.x — nueva API de statements financieros

**Error:** `AttributeError: 'EntityFacts' object has no attribute 'us_gaap'`

**Causa:** edgartools 5.x eliminó la API de bajo nivel `facts.us_gaap.ConceptName.to_dataframe()` y la reemplazó con statements financieros normalizados:

| Versión anterior | Versión 5.x |
|---|---|
| `facts.us_gaap.RevenueFromContract....to_dataframe()` | `facts.income_statement().to_dataframe()` |
| `facts.us_gaap.GrossProfit.to_dataframe()` | `facts.balance_sheet().to_dataframe()` |
| `facts.us_gaap.NetCashProvided....to_dataframe()` | `facts.cashflow_statement().to_dataframe()` |

Los tres métodos devuelven un `MultiPeriodStatement` cuyo `.to_dataframe()` tiene:
- **Índice**: nombre del concepto XBRL (e.g., `RevenueFromContractWithCustomerExcludingAssessedTax`)
- **Columnas**: `label`, metadatos, y columnas fiscales `FY 2025`, `FY 2024`, `FY 2023`...

```python
inc_df = facts.income_statement().to_dataframe()
# inc_df.loc['GrossProfit']['FY 2025'] → 195201000000.0
```

La ventaja del nuevo API: edgartools normaliza internamente los distintos nombres XBRL que cada empresa usa para el mismo concepto. Con el API viejo había que conocer exactamente qué nombre usaba cada empresa.

**Formato del `raw` dict preservado:** solo `fetch_raw` cambió internamente. El dict que devuelve (`"annual"`, `"balance_sheet"`, `"shares"`) es idéntico al anterior — `parse_financials` y todos los tests con fixtures no necesitaron cambios.

---

#### Fix 4: carácter Unicode `✓` en terminal Windows

**Error:** `UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'`

**Causa:** el terminal de Windows usa por defecto la codepage cp1252, que no incluye el carácter ✓ (U+2713). Rich intenta escribirlo y falla.

**Solución:** reemplazar `[green]✓[/green]` por `[green]OK[/green]` en el CLI. Simple y robusto en cualquier terminal.

**Lección:** en proyectos que corren en Windows, evitar caracteres Unicode decorativos (✓, →, ▶, emojis) en output de terminal a menos que se fuerce UTF-8 explícitamente (`PYTHONUTF8=1` o `Console(force_jupyter=False)`).

---

### Resultado del live run

```
Due Diligence Analysis — AAPL
Phase 1a: Supervisor + Financials specialist

OK Run ID:      a13c7077-51e
OK CIK:         0000320193
OK As of date:  2025-09-27

           Financials — 10-K FY2025
+--------------------------------------------------+
| Metric           | Value (USD B) | Period | Source     |
| Revenue          |         416.2 | FY2025 | XBRL       |
| Gross Profit     |         195.2 | FY2025 | XBRL       |
| Operating Income |         133.1 | FY2025 | XBRL       |
| Net Income       |         112.0 | FY2025 | XBRL       |
| Free Cash Flow   |          98.8 | FY2025 | calculated  |
| Total Debt       |          90.7 | FY2025 | XBRL       |
| Cash + STI       |          35.9 | FY2025 | XBRL       |
+--------------------------------------------------+

Specialists pending (Phase 1b): mgmt, filings, market, competitive
```

Todos los números vienen de EDGAR XBRL — cero hallucination. FCF marcado como `calculated` (OCF - Capex, no existe en el filing). Phase 1a verificada en producción.

---

*Última actualización: 2026-04-20 — Phase 1a completada con live run verificado.*
