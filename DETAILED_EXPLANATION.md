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

*Última actualización: 2026-04-19 — Phase 0 completada.*
