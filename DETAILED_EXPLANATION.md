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

---

---

## Phase 1b — Especialistas paralelos + HITL

### Contexto: ¿qué añade Phase 1b?

Phase 1a construyó un DAG lineal sin LLMs. Phase 1b introduce tres cosas nuevas:
1. **4 nodos especialistas** que hacen llamadas LLM reales para análisis cualitativo
2. **Fan-out con `Send()`** para ejecutarlos en paralelo
3. **HITL con `interrupt()`** para que el usuario revise datos antes de continuar

---

### Tarea previa: MCE-06 — hook de seguridad para `.env`

#### ¿Qué se hizo?
Se configuró un hook `PreToolUse` en `.claude/settings.json` que bloquea lecturas accidentales de archivos sensibles (`.env`, `*.pem`, `*.key`). El hook corre un script Python que inspecciona el path del archivo antes de permitir la lectura.

#### ¿Qué es un hook en Claude Code?
Los hooks son comandos shell que se ejecutan automáticamente en respuesta a eventos del ciclo de vida de Claude Code. El formato es:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Read",
      "hooks": [{ "type": "command", "command": "..." }]
    }]
  }
}
```

`PreToolUse` sobre `Read` corre antes de cada lectura de archivo. El comando recibe JSON por stdin con el path que se va a leer. Si devuelve `{"continue": false}`, la lectura se bloquea.

#### ¿Por qué Python en lugar de bash/jq?
En Windows, `jq` no está disponible por defecto. `uv run python` sí está — y el comando inline es suficientemente corto. El hook usa `re.search` para matchear patrones de rutas sensibles.

---

### ADR-012: Fuentes de datos del Management Agent

#### ¿Qué se decidió?
Antes de escribir el agente, se documentó en `docs/adr/ADR-012` qué fuentes usa y por qué. La decisión cubre el "EDGAR trifecta" + Tavily:

| Fuente | Qué aporta |
|---|---|
| DEF 14A (proxy statement) | Compensación ejecutiva, % ownership, composición del board |
| Form 4 | Compras/ventas de acciones por insiders en tiempo real |
| 10-K Item 10 | Bios de directores: formación, historial, experiencia |
| Tavily (búsqueda web) | Contexto cualitativo: entrevistas, noticias, reputación |

#### ¿Por qué no basta con los financials?
Los estados financieros muestran los *resultados* de la gestión, no las *características* de los gestores. Un CEO mediocre puede heredar un negocio excelente y hacer parecer buenos sus primeros años. Para juzgar la calidad del management necesitas saber: ¿cuánto tiempo lleva? ¿Tiene skin in the game? ¿Cómo compensa al equipo? ¿Ha asignado capital bien en el pasado? Ninguna de estas preguntas tiene respuesta en el income statement.

#### ¿Por qué Tavily y no OpenAI web search?
OpenAI tiene `web_search_preview` en su Responses API, pero usarlo requeriría cambiar el formato de llamada en nuestro LLM wrapper — un cambio que afecta a todo el sistema. Tavily es provider-agnostic: funciona igual independientemente de si el LLM backend es Claude u OpenAI. Free tier de 1000 búsquedas/mes es suficiente para uso personal.

---

### Task 1b-T1: `management_agent` — el primer especialista LLM

#### ¿Qué se construyó?

Tres archivos nuevos y dos extendidos:

```
src/due_diligence/
├── tools/
│   ├── edgar.py         ← + EdgarManagementAdapter (DEF 14A, Form 4, 10-K §10)
│   └── tavily.py        ← nuevo: TavilyAdapter + format_tavily_for_prompt()
└── agents/
    └── management.py    ← nuevo: nodo LangGraph completo
tests/
└── test_agents_1b.py    ← nuevo: 9 tests, 100% mockeados
```

#### ¿Cómo funciona el nodo por dentro?

El flujo del nodo `management_agent(state) -> dict`:

```
state["ticker"] = "AAPL"
    │
    ├─ EdgarManagementAdapter.fetch_def14a_text("AAPL")   → texto DEF 14A (12k chars)
    ├─ EdgarManagementAdapter.fetch_form4_transactions()  → lista de dicts
    ├─ EdgarManagementAdapter.fetch_10k_item10("AAPL")    → texto Item 10 (8k chars)
    └─ TavilyAdapter.search([4 queries])                  → lista de resultados web
    │
    └─ get_client().complete(system=_SYSTEM_PROMPT, messages=[contexto])
        │
        └─ _parse_llm_response(json_text, "AAPL")
            │
            └─ return {"mgmt": ManagementReport(stub=False, citations=[...])}
```

#### ¿Qué hace el LLM exactamente?

El LLM recibe todo el contexto (DEF 14A, Form 4, Item 10, Tavily) en el mensaje de usuario y una instrucción en el system prompt: "responde las 5 preguntas del §3 del checklist usando SOLO el contexto proporcionado. Devuelve JSON válido."

Lo que el LLM **hace**: interpreta texto, resume, extrae insights cualitativos.
Lo que el LLM **no hace**: inventar cifras. Los números (ownership %, compensation $) tienen que venir del texto de EDGAR que recibe — si no están, dice "not available". Esto cumple ADR-005.

#### Degraded mode — ¿qué pasa si Tavily no está configurado?

`TavilyAdapter.search()` comprueba si `settings.tavily_api_key` está vacío. Si lo está, devuelve lista vacía y loguea un warning. El agente continúa con solo las fuentes EDGAR. Esto sigue el principio "tools fallan loud" (ADR invariante #5): no aborta, pero sí avisa.

#### ¿Por qué `_parse_llm_response` tiene fallback?

Los LLMs ocasionalmente devuelven texto rodeando el JSON, o el JSON tiene un campo mal tipado. La función extrae el JSON buscando el primer `{` y el último `}`, intenta parsearlo, y si falla devuelve un `ManagementReport` parcial con el texto crudo en `compensation_structure_notes`. El agente nunca lanza excepción — siempre devuelve algo usable. Esto es consistente con "el sistema sigue funcionando aunque un agente falle".

#### `uv sync --extra dev` — el bug de entorno

Durante los tests descubrimos que `uv sync` (sin `--extra dev`) elimina las dependencias del grupo dev (pytest, pytest-mock, etc.) del entorno. El comando correcto para este proyecto es siempre:

```bash
uv sync --extra dev        # instala prod + dev
uv run python -m pytest    # usa el Python del venv (no el sistema)
```

`uv run pytest` (sin `python -m`) puede tomar el pytest del PATH del sistema, que usa Python 3.11 en lugar del Python 3.12 del venv. El síntoma es `ModuleNotFoundError: No module named 'due_diligence'` aunque el paquete esté instalado.

---

---

### Tarea 1b-T2 — Filings Agent (`agents/filings.py`)

#### ¿Qué cubre este agente?

El `filings_agent` responde las secciones **§1 (Business Understanding)** y **§7 (Risks & Red Flags)** del checklist canónico. Estas dos secciones son las que responde el propio 10-K anual de la empresa.

| Checklist | Fuente EDGAR | Pregunta clave |
|---|---|---|
| §1.1 Two-minute story | Item 1 (Business) | ¿Qué vende y cómo gana dinero? |
| §1.2 Revenue decomposition | Item 1 (Business) | Segmentos, geografías, % recurrente |
| §7.2 Regulatory exposure | Item 1A (Risk Factors) | Riesgos declarados formalmente |
| §7.3 Disruption vector | Item 1A (Risk Factors) | ¿Quién puede destruir el modelo? |
| §7.4 Accounting red flags | Item 7 (MD&A) | Cambios en políticas, restatements |
| §7.8 Cycle position | Item 7 (MD&A) | ¿En qué punto del ciclo opera? |

#### Por qué no usa Tavily (a diferencia del management agent)

El `management_agent` usa Tavily para complementar datos cualitativos de management que no están en los filings (track record, reputación, decisiones pasadas).

El `filings_agent` **no necesita Tavily** porque las secciones §1 y §7 están completamente cubiertas por el propio 10-K. Más aún: la empresa tiene obligación legal de declarar todos los riesgos materiales en el Item 1A. Si omite un riesgo relevante, se expone a responsabilidad civil. El 10-K es aquí la fuente más completa posible — no hay valor adicional de búsqueda web para estas preguntas.

#### Las tres secciones del 10-K y cómo las extrae edgartools

```python
tenk = filing.obj()                          # → TenK object de edgartools
business_text    = str(tenk.business or "")  # Item 1
risk_text        = str(tenk.risk_factors or "")  # Item 1A
mda_text         = str(tenk.management_discussion or "")  # Item 7
```

El objeto `TenK` de edgartools 5.x expone las secciones como atributos semánticos (no `item_1`, `item_1a`, sino `business`, `risk_factors`, `management_discussion`). Esto es importante — usar el nombre numérico lanzaría `AttributeError`.

Los tres métodos viven en `EdgarFilingsAdapter` en `tools/edgar.py`, separados de `EdgarManagementAdapter` para dejar claro qué datos sirven a qué agente.

#### El JSON que devuelve el LLM

```json
{
  "business_description": "Apple designs smartphones, computers...",
  "risk_factors_summary": "Key risks: App Store regulation, supply chain...",
  "mda_highlights": "FY2024 revenue $391B, services grew 13%...",
  "citations": [...]
}
```

El LLM recibe las tres secciones etiquetadas como `SOURCE 1/2/3` para que las citations apunten a `"10-K Item 1"`, `"10-K Item 1A"` o `"10-K Item 7"` correctamente.

#### Patrón compartido con management_agent

Los dos agentes son **idénticos en estructura** (fetch fuentes → prompt → LLM → parse → return dict). La diferencia es qué fuentes usan y qué sección del checklist cubren. En Phase 1b los dos siguientes agentes (`market_agent`, `competitive_agent`) usarán el mismo patrón.

---

---

### Tarea 1b-T3 — Market Agent (`agents/market.py`)

#### ¿Qué es diferente aquí? Tool-lane vs LLM-lane

Este es el primer agente de la "tool-lane" en Phase 1b. Los tres anteriores (financials, management, filings) usan LLM para interpretar texto. El market agent **no usa LLM** — los precios son datos exactos, no interpretables.

La distinción importa: si un agente LLM "interpreta" el precio, podría introducir sesgo en datos que deberían ser deterministas. El precio actual de AAPL es $182.52 — no hay interpretación posible. La interpretación (¿está barato o caro?) sucede en el `valuation_agent` de Phase 1c, cuando ya tiene todos los datos financieros.

#### Las dos fuentes y por qué cada una

| Fuente | Dato | Por qué esta fuente |
|---|---|---|
| **Polygon.io** | Precio actual, 52w high/low, market cap | Datos de mercado auditables. ADR-011 prohíbe Yahoo Finance/yfinance. |
| **Finnhub** | Noticias recientes (30 días) | Asocia noticias a tickers específicos — sin ambigüedad. |

#### Cómo funciona el 52-week range

No hay un endpoint directo "dame el 52w range". Se resuelve así:

```python
# Iterar todos los candles diarios del último año
for agg in client.list_aggs(ticker, 1, "day", one_year_ago, today, adjusted=True):
    highs.append(agg.high)
    lows.append(agg.low)

price_52w_high = max(highs)
price_52w_low = min(lows)
```

`list_aggs` devuelve un iterador de `Agg` (open/high/low/close/volume por día). Calculamos el máximo y mínimo sobre el año completo.

#### MetricValue en lugar de float

Los precios se devuelven como `MetricValue(value=182.52, unit="USD", period="2026-04-20", source="Polygon")`, no como floats desnudos. Esto es consistente con todo el sistema — cada número lleva su unidad, período y fuente para trazabilidad. El `valuation_agent` puede calcular ratios sabiendo exactamente qué representa cada valor.

#### Degraded mode simétrico

Los tres adapters (Tavily, Polygon, Finnhub) siguen el mismo patrón: si la API key no está configurada, devuelven un resultado vacío/none en lugar de lanzar excepción. El agente continúa y produce un reporte parcial. Esto permite desarrollar y testear sin necesitar todas las keys configuradas.

---

---

### Fix crítico: ROIC en `FinancialsReport`

#### El gap que existía

El sistema tenía revenue, márgenes, FCF, deuda — pero no ROIC. Sin ROIC no hay:
- §2.5: evidencia de moat estructural (ROIC >15% sostenido)
- §4.4: valor creado sobre el coste de capital (ROIC > WACC)
- Comparación con peers en el competitive_agent

#### Cómo se calcula ROIC en el sistema

```
effective_tax_rate = IncomeTaxExpenseBenefit / PreTaxIncome   (XBRL, most recent year)
NOPAT = OperatingIncomeLoss × (1 − effective_tax_rate)        (por año)
IC = StockholdersEquity + TotalDebt − Cash                    (punto en tiempo, más reciente)
ROIC = NOPAT / IC                                             (por año, usando IC más reciente)
```

**Nota importante sobre la aproximación:** usamos el IC del año más reciente para calcular el ROIC de todos los años de la serie. Para un análisis riguroso se necesitaría IC año a año, lo que requeriría stitch de múltiples filings. Para el propósito de este sistema (análisis de tendencia) es suficiente.

#### ROIC de Apple FY2025: 87%

Parece muy alto, pero es correcto. Apple tiene ROIC extremadamente alto porque:
1. **Asset-light model**: outsourcing total de manufactura (Foxconn)
2. **Buyback masivo**: equity book reducida a ~$73B con $2.8T de market cap
3. Con equity pequeña, el denominador IC es pequeño → ROIC enorme

Esta es exactamente la señal que busca el sistema: ROIC muy alto = moat estructural fuerte.

#### Nuevos campos en `FinancialsReport`

```python
roic_series: list[MetricValue]     # [(FY2025, 0.874), (FY2024, ...)]
invested_capital: MetricValue      # IC más reciente en USD
stockholders_equity: MetricValue   # Book equity más reciente
```

También se actualizó el fixture `aapl_xbrl_raw.json` a FY2025 (estaba stale en FY2024).

---

---

### Tarea 1b-T4 — Competitive Agent (`agents/competitive.py`)

#### ¿Qué lo hace diferente de los otros agentes LLM?

Es el único agente que **consume output de otro agente** como input. En lugar de fetchear EDGAR de nuevo, lee `state["financials"]` — el `FinancialsReport` ya calculado por `financials_node`. Esto es clave para la arquitectura fan-out: cada especialista produce un output independiente, pero pueden leer los outputs de los nodos anteriores.

```python
financials: FinancialsReport | None = state.get("financials")
financials_text = _format_financials_for_prompt(financials)
```

#### Los dos inputs al LLM

**Input 1 — Datos cuantitativos del FinancialsReport:**
```
ROIC series: FY2025: 87.4%, FY2024: 78.2%, FY2023: 65.1%
Gross margin: FY2025: 46.9%, FY2024: 46.2%, FY2023: 44.1%
Operating margin: FY2025: 31.9%, FY2024: 31.5%
Free cash flow: FY2025: $98.8B, FY2024: $108.8B
```

**Input 2 — Tavily web research:**
4 queries sobre competitive landscape, pricing power, disruption threats, margin benchmarks.

El LLM recibe ambos inputs etiquetados como fuentes separadas — puede citar "Financial data: ROIC series" o una URL de Tavily en las citations.

#### Validación del moat_classification

El LLM puede devolver cualquier string. El parser valida contra la lista permitida:

```python
moat = moat_raw if moat_raw in ("wide", "narrow", "none") else "none"
```

Si el LLM inventa "moderate" o "strong", se convierte a "none" en lugar de propagar un valor inválido.

#### Peer ROIC: deferred por ADR-005

`peer_roic_comparison` siempre queda vacío en Phase 1b. La razón: para comparar ROIC con peers de forma determinista (sin hallucination), necesitaría fetchear XBRL de 2-3 empresas adicionales — doblar el tiempo y coste del análisis. Se implementa en Phase 1c, cuando el valuation_agent tenga el contexto completo para elegir los peers correctos.

---

*Última actualización: 2026-04-20 — Phase 1b COMPLETA. Los 4 especialistas implementados (70 tests, verde).*

---

---

### Tarea 1b-T5 — Fan-out con `Send()` y checkpoint HITL (`graph.py` + `checkpoint.py`)

#### ¿Qué se construyó?

El cableado que conecta los 4 especialistas al grafo y los ejecuta en paralelo, con un punto de pausa HITL entre la fase de análisis y la futura fase de valoración.

```
src/due_diligence/
├── agents/
│   └── checkpoint.py    ← nuevo: checkpoint1_node con interrupt()
└── graph/
    └── graph.py         ← actualizado: fan-out Send() × 4 + checkpoint1

tests/
└── test_graph_1b.py     ← nuevo: 7 tests de estructura + comportamiento
```

---

#### El fan-out con `Send()` — paralelismo en LangGraph

En Phase 1a el grafo era lineal: `supervisor → financials → END`. Los 4 especialistas de Phase 1b son **independientes entre sí** — no necesitan esperar al resultado de los otros. Esto es un caso perfecto para paralelismo.

LangGraph implementa el fan-out con `Send()`:

```python
from langgraph.types import Send

def _route_to_specialists(state: AnalysisState) -> list[Send]:
    return [
        Send("management", state),
        Send("filings", state),
        Send("market", state),
        Send("competitive", state),
    ]

builder.add_conditional_edges("financials", _route_to_specialists)
```

`add_conditional_edges` recibe una función que devuelve a qué nodo ir a continuación. Cuando esa función devuelve `list[Send]` en lugar de un string, LangGraph ejecuta **todos en paralelo**. Cada `Send()` recibe una copia del estado actual, lo que garantiza que los nodos no interfieren entre sí mientras corren.

**El fan-in es automático**: cuando los 4 especialistas terminan, LangGraph hace el merge de sus 4 dicts de retorno en el estado compartido. No hay que sincronizar manualmente — es parte del contrato de `Send()`.

#### HITL con `interrupt()` — Human In The Loop

```python
from langgraph.types import interrupt

def checkpoint1_node(state: AnalysisState) -> dict:
    user_input = interrupt({
        "phase": "checkpoint1",
        "ticker": state["ticker"],
        "message": "Phase 1b complete. Review and confirm to proceed to valuation.",
        "available_reports": [...],
    })
    return {"checkpoint1_user_input": user_input or {}}
```

`interrupt()` hace tres cosas:
1. **Serializa el estado completo** al checkpointer (MemorySaver en tests, SqliteSaver en producción).
2. **Pausa el grafo** — `graph.invoke()` retorna el estado actual sin completar el run.
3. **Espera input** — el grafo se reanuda cuando el usuario llama `graph.invoke(Command(resume=value), config=config)`.

En el diagrama de flujo:

```
financials_node
    │
    ├─ Send("management", state) ──→ management_agent  ─┐
    ├─ Send("filings", state)    ──→ filings_agent     ─┤
    ├─ Send("market", state)     ──→ market_agent      ─┤ merge automático
    └─ Send("competitive", state)──→ competitive_agent ─┤
                                                         ▼
                                                 checkpoint1_node
                                                 interrupt() ← pausa aquí
                                                         │
                                                         ▼ (Phase 1c)
                                                 valuation_agent → ...
```

#### Comportamiento de `interrupt()` — lo que aprendimos al testear

Había una suposición incorrecta que los tests corrigieron: se asumía que `graph.invoke()` lanzaría `GraphInterrupt` al encontrar un `interrupt()`. La realidad es diferente:

| Contexto | Comportamiento real |
|---|---|
| `interrupt()` llamado **fuera del grafo** (directo) | Lanza `RuntimeError("Called get_config outside of a runnable context")` |
| `graph.invoke()` **con MemorySaver** llega a `interrupt()` | `invoke()` retorna normalmente con el estado actual — NO propaga excepción |
| Para verificar que el grafo está pausado | `graph.get_state(config).next == ("checkpoint1",)` |
| Para reanudar | `graph.invoke(Command(resume=user_input), config=config)` |

LangGraph captura internamente la señal de interrupt y gestiona la pausa sin exponerla como excepción al llamador. Esto hace que el código del cliente sea más limpio — no necesita `try/except`.

#### Los tests del grafo Phase 1b

`test_graph_1b.py` cubre tres categorías:

**Estructura estática** (sin correr el grafo):
- `test_build_graph_1b_has_all_specialist_nodes` — los 5 nuevos nodos están registrados
- `test_route_to_specialists_returns_four_sends` — la función de routing devuelve 4 `Send()`
- `test_route_to_specialists_targets` — los 4 `Send()` apuntan a los nodos correctos

**Comportamiento del checkpoint**:
- `test_checkpoint1_raises_outside_runnable_context` — `interrupt()` fuera del grafo lanza `RuntimeError`

**End-to-end** (grafo completo con MemorySaver + todo mockeado):
- `test_graph_1b_fanout_pauses_at_checkpoint1` — verifica que el grafo pausa y `get_state().next == ("checkpoint1",)`
- `test_graph_1b_state_has_all_four_reports_after_fanout` — los 4 reports con `stub=False` están en el estado tras el fan-out

#### Test stale de Phase 1a que se actualizó

`test_graph_1a_state_has_no_stub_fields_for_later_agents` verificaba que `mgmt`, `filings`, etc. **no existían** en Phase 1a. Con Phase 1b cableada, ahora sí existen. El test se renombró a `test_graph_1b_state_has_specialist_fields` y sus aserciones se invirtieron: verifica que los 4 campos **sí existen** tras el fan-out.

Este cambio de dirección es normal en desarrollo incremental: los tests de "esto todavía no existe" tienen fecha de caducidad — cuando se implementa la feature, el test cambia de "no debe existir" a "debe existir y con estas propiedades".

---

### Phase 1b — Visión de conjunto final

Con los 5 tasks completados, Phase 1b está terminada. El estado del sistema:

```
src/due_diligence/
├── tools/
│   ├── edgar.py        ← EdgarAdapter + EdgarFilingsAdapter + EdgarManagementAdapter + ROIC
│   ├── tavily.py       ← TavilyAdapter (búsqueda web)
│   ├── polygon.py      ← PolygonAdapter (precios, market cap)
│   └── finnhub.py      ← FinnhubAdapter (noticias)
├── agents/
│   ├── supervisor.py   ← ticker→CIK, as_of_date, run_id
│   ├── financials.py   ← EdgarAdapter wrapper, tool-lane
│   ├── management.py   ← LLM-lane: DEF14A + Form4 + §10 + Tavily → §3 checklist
│   ├── filings.py      ← LLM-lane: 10-K §1/1A/7 → §1 + §7 checklist
│   ├── market.py       ← tool-lane: Polygon + Finnhub (sin LLM)
│   ├── competitive.py  ← LLM-lane: state["financials"] + Tavily → §2 checklist
│   └── checkpoint.py   ← HITL: interrupt() entre Phase 1b y 1c
└── graph/
    ├── state.py        ← AnalysisState (ampliado con checkpoint inputs)
    └── graph.py        ← build_graph() con fan-out Send() + checkpoint1
```

Tests: 77 passing, 0 real API calls.

Flujo completo Phase 1b:
```
START → supervisor → financials → [management | filings | market | competitive] → checkpoint1(HITL) → END
```

Lo que Phase 1c añadirá: `valuation_agent`, `value_expert` (síntesis + VERDICT), `critic_agent`, y `checkpoint2` (segundo HITL antes del veredicto final).

---

*Última actualización: 2026-04-20 — Phase 1b COMPLETA. Fan-out + HITL cableados. 77 tests, verde.*

---

---

## Phase 1c — Valuation + Value Expert + Critic + Archiver

### Contexto: ¿qué cierra Phase 1c?

Phase 1b terminó con el grafo pausado en `checkpoint1`, con los 4 especialistas completos pero sin ningún juicio de inversión. Phase 1c añade la segunda mitad: el cálculo de valor intrínseco y la síntesis final con VERDICT.

```
checkpoint1 → checkpoint2 → valuation → value_expert → critic → archiver → END
```

---

### Fix previo: modelos OpenAI actualizados

Antes de implementar Phase 1c, se actualizaron dos decisiones de configuración:

1. **`openai_fallback_model`**: `gpt-4o-mini` → `gpt-4.1` — el mini era demasiado débil para síntesis cualitativa.
2. **Nuevo `openai_expert_model`**: `gpt-5.4-mini` — para value_expert y critic, donde la calidad de razonamiento importa más.
3. **Fix en `_complete_openai()`**: el método ahora acepta un `model` parameter explícito. Antes ignoraba el modelo solicitado y siempre usaba el fallback. Ahora los agentes pueden especificar qué modelo OpenAI quieren directamente.

---

### Tarea 1c-T1: `checkpoint2_node` + `valuation_agent`

#### checkpoint2 — el segundo HITL

`checkpoint2_node` pausa el grafo justo antes de que corra la valoración. Muestra al usuario el FCF, market cap y ROIC disponibles, y pide los assumptions del DCF:

```python
user_input = interrupt({
    "phase": "checkpoint2",
    "context": {"latest_fcf": "$98.8B (FY2025)", "market_cap": "$2800B", ...},
    "defaults": {"wacc_pct": 9.0, "terminal_growth_pct": 2.5, "projection_years": 10},
})
return {"checkpoint2_user_input": user_input or {}}
```

Si el usuario reanuda con `{}`, el `valuation_agent` usa los defaults internos.

#### valuation_agent — aritmética pura, zero LLM

El único agente del sistema que hace cálculos matemáticos explícitos. No llama al LLM — todos los números salen de Python.

**DCF en dos etapas:**
```
Phase 1 (years 1–N):  FCF crece a near_term_growth por año
Phase 2 (terminal):   FCF crece a terminal_growth en perpetuidad (Gordon Growth Model)

PV = Σ FCF_t/(1+WACC)^t + [FCF_N * (1+g) / (WACC−g)] / (1+WACC)^N
```

El near-term growth se determina así (en orden de prioridad):
1. Si el usuario lo proporcionó en checkpoint2 → se usa ese
2. Si hay ≥2 puntos de FCF histórico → se calcula el CAGR (capped a [−15%, +30%])
3. Default: 7%

**Reverse DCF** — búsqueda binaria de 60 iteraciones para encontrar el growth que iguala el DCF al market cap actual. Responde: "¿qué crecimiento está pagando el mercado hoy?"

**Sensitivity table 3×3**: WACC ± 1% × near-term growth ± 5%. Permite ver el rango de valores razonables sin re-ejecutar el análisis.

**Múltiplos deterministas**: EV/EBIT, EV/FCF, P/E, earnings yield (EBIT/EV). Todos desde EDGAR + Polygon.

---

### Tarea 1c-T2: `value_expert_agent`

#### El cerebro del sistema

Es el único agente que recibe todos los reports simultáneamente y los sintetiza en un juicio de inversión. La diferencia respecto a los agentes de Phase 1b:

| Agentes Phase 1b | Value Expert |
|---|---|
| Una fuente de datos | Todos los reports |
| Análisis acotado | Síntesis transversal |
| Output factual | Output interpretativo + VERDICT |

#### Las 9 secciones del checklist

El sistema prompt instruye al LLM a cubrir exactamente las 9 secciones del `checklist-value-investing.md`:

```
§1 Business understanding   §2 Moat analysis        §3 Management assessment
§4 Financial quality        §5 Growth/reinvestment  §6 Valuation
§7 Risks & red flags        §8 Psychological lens   §9 Decision frame
```

Más el VERDICT: `BUY | HOLD | SELL` + confidence (0-1) + suggested_sizing_pct (0-20%).

#### VERDICT rubric (en el system prompt)

```
BUY:  moat claro + management alineado + DCF upside >20% + sin red flags materiales
HOLD: señales mixtas, valoración justa, o datos insuficientes
SELL: moat deteriorando O value trap O DCF downside >20% con red flags
```

#### Invariante ADR-009

El system prompt incluye explícitamente: "Never generate numbers not present in the provided context." Si el LLM intenta inventar una cifra, la instrucción lo prohíbe. El VERDICT es una interpretación — nunca un número nuevo.

---

### Tarea 1c-T3: `critic_agent`

#### El problema del sesgo de confirmación

Si pedimos al value_expert que critique su propio análisis, tiende a minimizar los riesgos ("si bien existen riesgos, consideramos que..."). Es el problema de confirmation bias estructural en los LLMs cuando se les pide auto-crítica.

La solución: el critic es un agente separado con instrucciones opuestas. Su única misión es encontrar fallos.

#### Lo que el critic NO ve

El critic recibe los datos crudos (financials, risk factors, valuation numbers) y el VERDICT del Expert — pero **no** recibe el razonamiento completo del Expert. Esto evita el efecto de anclaje: el critic no puede racionalizar su camino hacia la misma conclusión que el Expert si no conoce el argumento.

#### Output del critic

```json
{
  "weaknesses": ["debilidad específica 1", "debilidad específica 2", ...],
  "contra_thesis": "El argumento más sólido para NO comprar.",
  "pre_mortem_scenario": "En 3 años, el thesis falló porque..."
}
```

Nota importante: el critic no emite un VERDICT alternativo. Su rol no es contradecir — es añadir perspectiva. Eduardo toma la decisión final viendo ambas caras.

---

### Tarea 1c-T4: `archiver_node`

#### Dos formatos, dos propósitos

**Markdown** (`data/reports/AAPL-2026-04-21.md`):
- Legible por humanos, abre en cualquier editor
- Contiene: header con VERDICT, tabla de valoración, sensitivity table, 9 secciones del Expert, bullets del Critic
- En Phase 2 se indexará para RAG comparativo entre análisis

**SQLite** (`data/analyses.db`, tabla `analyses`):
- Una fila por análisis ejecutado
- Campos: ticker, run_id, as_of_date, verdict, confidence, sizing_pct, dcf_upside, moat, report_path
- Permite queries: "dame todos los BUY con ROIC > 20%"
- En Phase 2 será la fuente del sistema de memoria

#### El archiver devuelve `{}`

Es el único nodo del grafo que devuelve un dict vacío. Su trabajo es un efecto secundario (I/O) — no produce ningún output que otros nodos necesiten consumir. LangGraph acepta `{}` como return válido de un nodo.

---

### Tarea 1c-T5: CLI `analyze` — el loop de checkpoints

#### Por qué un loop y no un invoke() único

Phase 1a usaba un único `graph.invoke()` porque el grafo era lineal. Con dos `interrupt()`, el patrón cambia:

```python
# Cada invoke() corre hasta el próximo interrupt() o hasta END
graph.invoke({"ticker": ticker}, config=config)          # corre hasta checkpoint1
graph.invoke(Command(resume={}), config=config)           # corre hasta checkpoint2
graph.invoke(Command(resume=assumptions), config=config)  # corre hasta END
```

`Command(resume=value)` es el objeto de LangGraph para reanudar un grafo pausado. El mismo `config` (con el mismo `thread_id`) le dice al checkpointer en qué estado estaba.

#### El diseño de la UX en cada checkpoint

**Checkpoint 1** — el CLI muestra una tabla con los 4 specialists y sus resultados clave (moat, insider %, precio, FCF). Pide confirmación simple: "¿Continuar? [s/n]"

**Checkpoint 2** — muestra FCF, market cap y ROIC disponibles como contexto, y pide los 4 assumptions del DCF con defaults interactivos:
```
WACC % [9.0]: _
Terminal growth % [2.5]: _
Projection years [10]: _
Near-term FCF growth % [auto from history]: _
```

**Output final** — panel con VERDICT (verde/amarillo/rojo según BUY/HOLD/SELL), 9 secciones del Expert, bullets del Critic, ruta del markdown guardado.

---

### Phase 1c — Visión de conjunto final

Con los 5 tasks completados, Phase 1 (a+b+c) está terminada:

```
src/due_diligence/
├── tools/           edgar, tavily, polygon, finnhub
├── agents/
│   ├── supervisor, financials, management, filings, market, competitive
│   ├── checkpoint (checkpoint1 + checkpoint2)
│   ├── valuation, value_expert, critic, archiver
└── graph/           state, graph (12 nodos)
```

Tests: 152 passing, 0 real API calls.

Flujo completo:
```
START → supervisor → financials
      → [management | filings | market | competitive]  ← paralelo via Send()
      → checkpoint1 (HITL: review)
      → checkpoint2 (HITL: DCF assumptions)
      → valuation → value_expert → critic → archiver → END
```

Comando de producción: `uv run due-diligence analyze AAPL`

Lo que Phase 2 añadirá: ChromaDB local con embeddings de los reports archivados, tool `retrieve_similar_analyses` para el value_expert, y news ingestion continuo para el market agent.

---

*Última actualización: 2026-04-21 — Phase 1c COMPLETA. 152 tests, verde. CLI con checkpoints interactivos.*

---

---

### Issues descubiertos en el primer live run

#### Issue 1: SqliteSaver corrompe los objetos Pydantic entre reanudaciones

**Síntoma:** El CLI llegaba hasta checkpoint2, pero Phase 1c (valuation, value_expert, critic, archiver) nunca ejecutaba. El report quedaba vacío.

**Causa raíz:** LangGraph serializa el estado del grafo con msgpack cuando usa `SqliteSaver`. Msgpack no conoce los tipos Pydantic del proyecto (`FinancialsReport`, `ManagementReport`, etc.). Al restaurar el estado entre los tres `invoke()` del CLI, los objetos Pydantic llegaban corruptos o como dicts — lo que hacía que los nodos de Phase 1c fallaran silenciosamente.

**Por qué es un problema de Phase 1:** El CLI interactivo hace tres invocaciones separadas al grafo:
```python
graph.invoke({"ticker": ...})           # corre hasta checkpoint1
graph.invoke(Command(resume={}))        # corre hasta checkpoint2
graph.invoke(Command(resume=assumptions)) # corre hasta END
```
Entre cada invocación, LangGraph serializa el estado completo (con todos los objetos Pydantic) a SQLite y lo restaura en la siguiente llamada. Si la serialización falla, el estado llega roto.

**Solución Phase 1:** Cambiar `SqliteSaver` → `MemorySaver` en el CLI. Estado en RAM durante el run — sin serialización/deserialización entre reanudaciones. Los tres `invoke()` comparten el mismo proceso Python, así que los objetos Pydantic no se tocan.

```python
# Antes (roto):
conn = sqlite3.connect(db_path)
checkpointer = SqliteSaver(conn)

# Ahora (funciona):
checkpointer = MemorySaver()
```

**Consecuencia asumida:** Si el usuario cierra el terminal a mitad del análisis, pierde el progreso. Aceptable para Phase 1 — el run completo dura ~2 minutos.

**La persistencia real NO depende del checkpointer.** El `archiver_node` (que corre al final del grafo) escribe el report a `data/reports/<TICKER>-<DATE>.md` y una fila a `data/analyses.db`. El checkpointer es solo para los interrupts HITL durante el run — no para persistir el análisis entre sesiones.

**Solución definitiva para Phase 5 (webapp):** Cuando el grafo corra en background y el usuario quiera reanudar tras cerrar el navegador, se necesita `SqliteSaver` con serialización correcta. La solución será implementar `__get_pydantic_core_schema__` en los modelos Pydantic para que msgpack pueda serializarlos, o usar `JsonPlusSerializer` correctamente. Se documentará en un ADR al arrancar Phase 5.

---

#### Issue 2: El log mostraba `model=claude-sonnet-4-5` al usar OpenAI

**Síntoma:** Los logs mostraban `model=claude-sonnet-4-5 provider=openai` — un nombre de modelo Claude mientras se usaba OpenAI.

**Causa:** `client.py` logueaba `settings.default_model` (un nombre Claude) antes de resolver al modelo OpenAI real.

**Fix:** El cliente ahora resuelve el modelo OpenAI real antes de loguearlo:
```python
oai_model = model if not model.startswith("claude") else settings.openai_fallback_model
log.info("llm_complete", provider="openai", model=oai_model)
```

---

#### Issue 3: polygon.io → massive.com (renombrado)

**Síntoma:** polygon.io redirige a massive.com. El OAuth de registro entraba en bucle.

**Diagnóstico:** Polygon.io fue adquirida y renombrada a Massive. Solo el sitio web cambió — el API endpoint `https://api.polygon.io` sigue funcionando igual. El paquete `polygon-api-client` no requiere ningún cambio.

**Para registrarse:** ir a massive.com, usar email/password (no OAuth). La API key obtenida funciona con el código existente sin modificaciones.

---

#### Issue 4 (causa raíz real): `Command(resume={})` con dict vacío no funciona en LangGraph

**Síntoma:** Phase 1c nunca ejecutaba. `get_state().next` seguía siendo `('checkpoint1',)` después de `invoke(Command(resume={}))`.

**Causa raíz:** En LangGraph, `Command(resume=valor)` requiere un valor **truthy**. El dict vacío `{}` es falsy en Python — LangGraph lo trata como "sin valor de resume" y no avanza el grafo. El checkpoint vuelve a interrumpir inmediatamente.

**Cómo se encontró:** Añadiendo diagnóstico `get_state().next` después de cada invoke. Output revelador:
```
DEBUG after cp1 resume: next=('checkpoint1',)  ← el graph no avanzó
DEBUG after cp2 resume: next=('checkpoint2',)  ← ídem
```

Después, tests de bisección aislaron que `Command(resume="proceed")` funcionaba pero `Command(resume={})` no.

**Fix:**
```python
# Roto:
graph.invoke(Command(resume={}), config=config)

# Funciona:
graph.invoke(Command(resume={"proceed": True}), config=config)
```

También actualizado en `checkpoint1_node`: el nodo ahora devuelve `{"proceed": True}` como fallback en lugar de `{}`.

**Nota importante para futuro:** cualquier `Command(resume=valor)` en LangGraph debe usar un valor truthy. Evitar `{}`, `None`, `False`, `0` como valores de resume.

---

#### Issue 5: GPT-5.x usa `max_completion_tokens`, no `max_tokens`

**Síntoma:** `BadRequestError: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.`

**Causa:** OpenAI cambió el nombre del parámetro en los modelos GPT-5.x. Los modelos 4.x aceptan `max_tokens`; los 5.x requieren `max_completion_tokens`.

**Fix:** En `client.py`, cambiar `max_tokens=max_tokens` por `max_completion_tokens=max_tokens`. El parámetro nuevo es compatible con todos los modelos OpenAI actuales.

---

#### Issue 6: `polygon-api-client` devuelve lista en `get_previous_close_agg`

**Síntoma:** `warning: polygon_prev_close_failed error="'list' object has no attribute 'close'"`. El precio actual siempre era `None`.

**Causa:** El SDK de Polygon cambió la API — `get_previous_close_agg()` ahora devuelve `[PreviousClose]` (lista) en lugar de `PreviousClose` (objeto único).

**Fix:** Detección dinámica del tipo de retorno:
```python
prev_result = client.get_previous_close_agg(ticker, adjusted=True)
prev = prev_result[0] if isinstance(prev_result, list) else prev_result
price_current = prev.close
```

---

*Última actualización: 2026-04-21 — Phase 1 COMPLETA (1a + 1b + 1c). Live run verificado con AAPL. 152 tests, verde.*

---

## Phase 2 — Transparencia, nuevas fuentes, síntesis real

### Task 1: Citations visibility + Market & News en el markdown report (ADR-014)

#### Concepto

Una **cita** (`Citation`) es un objeto Pydantic que cada agente LLM produce cuando hace una afirmación verificable. Contiene:
- `claim`: la afirmación textual ("Tim Cook owns 3.3M shares")
- `source_url`: la URL del documento que la respalda (DEF14A en EDGAR, transcript de Finnhub, etc.)
- `excerpt`: el fragmento verbatim del documento que contiene la evidencia
- `confidence`: un score 0.0-1.0 del match (verificado por substring-match en Phase 1)

#### Por qué existe

ADR-005 establece que **todo claim cualitativo debe rastrearse a fuente primaria**. Phase 1 construyó la captura: cada agente LLM (`management`, `filings`, `competitive`) emite citas validadas como parte de su reporte Pydantic. Pero el `archiver_node` las ignoraba al renderizar el markdown — el trail de auditoría existía en memoria pero era invisible en el artefacto final.

Task 1 completa la segunda mitad del ADR-005: la visualización. Sin ella, el sistema podía afirmar que "no alucinó" pero el lector no podía comprobarlo.

#### Cómo funciona

```
_render_markdown(state)
    │
    ├─ ... (secciones existentes: valuation, expert, critic)
    │
    ├─ ## Market & News          ← NUEVO: MarketReport.recent_news_summary con URLs inline
    │
    ├─ _render_citations_section(state)   ← NUEVO
    │   ├─ state["mgmt"].citations
    │   ├─ state["filings"].citations
    │   └─ state["competitive"].citations
    │
    └─ --- footer ---
```

`_render_citations_section` itera sobre los tres agentes LLM, agrupa sus citas por agente, y renderiza cada una:

```markdown
## Sources & Citations

### Management
- **Tim Cook owns 3.3M shares** (confidence: 95%)
  > Timothy D. Cook beneficially owns 3,279,321 shares
  Source: [https://sec.gov/.../DEF14A.htm](https://sec.gov/.../DEF14A.htm)
```

Si ningún agente tiene citas (o todos son stubs), la función devuelve `[]` y la sección no aparece — degraded mode limpio.

#### Relación con la arquitectura

- No toca ningún agente, schema, ni nodo del grafo. Solo `archiver.py`.
- El `MarketReport.recent_news_summary` ya contenía URLs inline (formato Finnhub/Tavily). Añadir la sección `## Market & News` al render es la forma más simple de exponerlas.
- El formato de la sección Citations (claim + excerpt + URL) está pensado para que en Phase 2.5 (RAG) el parser de corpus pueda extraer claim-evidence pairs automáticamente.

#### Archivos modificados

- `src/due_diligence/agents/archiver.py`: añadida `_render_citations_section()` + bloque Market & News en `_render_markdown`.
- `tests/test_archiver.py`: 3 tests nuevos — citations section presente, citations section ausente cuando vacía, Market & News section con URL inline.

---

### Task 3: Peer ROIC comparison — user-supplied (ADR-015)

#### Concepto

**Peer ROIC comparison** es la capacidad del sistema de mostrar, junto al ROIC de la empresa analizada, los ROICs de 3-5 empresas comparables elegidas por el usuario. El resultado es una tabla en el reporte markdown que permite contextualizar: "¿es el ROIC de AAPL del 25% alto o bajo respecto a sus peers reales?"

#### Por qué user-supplied y no automático

La alternativa obvia (descubrir peers por SIC code SEC) se rechazó en ADR-015 porque los SIC codes son ruidosos — un mismo código puede agrupar empresas sin relación económica real. Eduardo conoce mejor sus peers que cualquier heurística. Además, *preguntar* tiene valor Socratic: te fuerza a articular explícitamente "¿contra quién compite esta empresa hoy?".

El skip está habilitado explícitamente: si pulsas Enter sin escribir nada, el sistema continúa normalmente y el reporte dice "Peer comparison skipped by user" en lugar de la tabla.

#### Cómo funciona — el flujo en el grafo

```
checkpoint1_node  ←── usuario escribe peers en CLI
      │
      ▼
peers_node                           ← NUEVO: tool-lane, sin LLM
   para cada peer en state["peers"]:
     EdgarAdapter.fetch_raw(peer)
     EdgarAdapter.parse_financials(raw)
   → state["peer_financials"] = [FinancialsReport, ...]
      │
      ▼
checkpoint2_node
      │
      ▼
competitive_agent  ←── lee state["peer_financials"]
   construye list[PeerROIC]
   → CompetitiveReport.peer_roic_comparison = [PeerROIC(...), ...]
      │
      ▼
archiver_node  ←── renderiza tabla ## Peer Comparison — ROIC
```

El patrón es el mismo que `financials_node`: `EdgarAdapter.fetch_raw` + `parse_financials` = cero nueva superficie de tooling, cero LLM para los números.

#### Degraded mode

- `peers=[]` (skip) → `peers_node` devuelve `[]` inmediatamente, el archiver muestra "Peer comparison skipped by user".
- Un peer con fallo (ticker inválido, XBRL corrupto) → `log.warning` + omisión, el resto continúa. Fail-loud por peer, no por batch.

#### Archivos modificados

- `src/due_diligence/graph/state.py`: añadidos `peers: NotRequired[list[str]]` y `peer_financials: NotRequired[list[FinancialsReport]]`.
- `src/due_diligence/schemas/reports.py`: nuevo modelo `PeerROIC(ticker, roic, period)`, campo `peer_roic_comparison: list[PeerROIC]` en `CompetitiveReport`.
- `src/due_diligence/agents/peers.py`: nuevo nodo `peers_node`.
- `src/due_diligence/agents/competitive.py`: construye `list[PeerROIC]` desde `state["peer_financials"]` y lo inyecta en el reporte.
- `src/due_diligence/agents/checkpoint.py`: incluye prompt de peers en el payload del interrupt.
- `src/due_diligence/graph/graph.py`: inserta `peers_node` entre `checkpoint1` y `checkpoint2`.
- `src/due_diligence/cli.py`: prompt de peers con skip explícito; pasa `peers_raw` en el `Command(resume=...)`.
- `src/due_diligence/agents/archiver.py`: renderiza tabla ROIC o mensaje "skipped".
- `tests/test_peers_agent.py`: 5 tests — fetch correcto, lista vacía, key ausente, fallo por peer, excepción inesperada.

---

### Task 4: Earnings call transcripts como primary source (ADR-016)

#### Concepto

Un **earnings call transcript** es la transcripción de la llamada trimestral en que el management presenta resultados y responde preguntas de analistas. Finnhub los sirve estructurados por speaker y sección (prepared remarks vs Q&A).

La diferencia respecto a los 10-K es fundamental: el 10-K está revisado por abogados y diseñado para compliance. La Q&A de un earnings call es tiempo real — el CEO responde en vivo a preguntas hostiles sobre márgenes, guidance, competencia. La evasión, la precisión, la confianza con la que citan sus propios números — todas son señales de calidad de management que el 10-K no captura.

#### Cómo funciona

```
FinnhubAdapter.fetch_transcripts(ticker, quarters=4)
    │  transcripts_list(ticker) → lista de {id, year, quarter, time}
    │  Ordena desc, toma N más recientes
    │  Para cada uno: transcripts(id) → {transcript: [{speaker, speech}, ...]}
    │  Separa prepared_remarks / Q&A por heurístico "Q&A" en los primeros 80 chars
    │
    ▼  list[dict]
_format_transcripts(transcripts)
    │  "N recent earnings calls (most recent first):"
    │  Para cada uno: head + prepared (hasta 2000 chars) + Q&A (hasta 3000 chars)
    ▼  str
management_agent → SOURCE 5 en _build_user_prompt
```

La función respeta el patrón degraded mode: si `FINNHUB_API_KEY` no está configurada, devuelve `[]` inmediatamente — el prompt incluye "No earnings call transcripts available." y el LLM continúa con las 4 fuentes existentes.

#### Relación con la arquitectura

- Cero cambio de schema. `ManagementReport.citations` ya existe — el LLM puede citar frases de transcripts igual que cita fragmentos del DEF 14A.
- Cero cambio de grafo. `management_agent` ya es un nodo en el fan-out; simplemente tiene un SOURCE más.
- El `_build_user_prompt` recibe `transcripts_text: str = ""` con default vacío — los tests existentes no necesitan actualizarse.

#### Archivos modificados

- `src/due_diligence/tools/finnhub.py`: nuevo método `FinnhubAdapter.fetch_transcripts` + helper `_format_transcripts` en `management.py`.
- `src/due_diligence/agents/management.py`: import de `FinnhubAdapter`, llamada en el agente, SOURCE 5 en el prompt, regla de citación de transcripts en `_SYSTEM_PROMPT`.
- `tests/test_finnhub_transcripts.py`: 4 tests — lista correcta, sin key, fallo de lista, fallo de transcript individual.

---

### Task 5: 8-K event tracking desde EDGAR (ADR-017)

#### Concepto

Los **8-K** son filings ad-hoc que las empresas presentan en SEC EDGAR cuando ocurre un evento material — fuera del ciclo anual del 10-K. Son la fuente más autoritativa para detectar cambios entre los filings anuales, sin depender de APIs de noticias comerciales.

Los tres Items que el sistema rastrea:
- **2.02** — Results of Operations (earnings release): confirmación primaria de resultados trimestrales.
- **4.02** — Non-Reliance on Previously Issued Financial Statements: **restatement contable**, la señal roja más crítica disponible en EDGAR. Un Item 4.02 reciente debería degradar automáticamente cualquier VERDICT hacia HOLD o SELL.
- **5.02** — Departure/Appointment of Directors and Officers: cambios de CEO/CFO que el 10-K anual podría no reflejar si ocurrieron a mitad del año.

#### Por qué 12 meses (no 6)

Los Items 4.02 y 5.02 no prescriben en 6 meses — un restatement de hace 10 meses sigue siendo relevante para evaluar la calidad del reporting, y un CEO que tomó el cargo hace 9 meses sigue siendo "nuevo" en términos de track record. El año completo captura el ciclo fiscal sin añadir ruido de eventos demasiado antiguos. Configurable con `EVENTS_LOOKBACK_MONTHS` en `.env`.

#### Cómo funciona

```
market_agent
    EdgarEventsAdapter.fetch_8k_events(ticker, months=12)
        │  Company(ticker).get_filings(form="8-K", date="YYYY-MM-DD:")
        │  Para cada filing: f.obj().items → filtrar por [2.02, 4.02, 5.02]
        │  summary_text[:600], filing_url
        ▼  list[dict]
    MarketReport.material_events = [...]
        │
archiver_node → ## Material Events (8-K, last 12 months)
    │  Si hay eventos: tabla | Date | Item(s) | Summary | Source |
    │  Si no hay: "No material 8-K events in the last 12 months."
    │  (fail-loud: siempre presente si market report existe)
```

#### Archivos modificados

- `src/due_diligence/tools/edgar.py`: nueva clase `EdgarEventsAdapter` con `fetch_8k_events`.
- `src/due_diligence/schemas/reports.py`: campo `material_events: list[dict]` en `MarketReport`.
- `src/due_diligence/config.py`: setting `EVENTS_LOOKBACK_MONTHS` (default 12).
- `src/due_diligence/agents/market.py`: llama a `fetch_8k_events`, popula campo.
- `src/due_diligence/agents/archiver.py`: tabla de eventos o mensaje explícito "no events".
- `tests/test_edgar_events.py`: 4 tests — filtrado por item, sin coincidencias, múltiples items, fallo de filing individual.
