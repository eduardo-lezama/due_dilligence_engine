import json
import os
import unittest.mock
from pathlib import Path

# Settings() exige SEC_USER_AGENT al IMPORTAR due_diligence.config, antes de que corra
# cualquier fixture. Sin esto la suite dependía de que existiera un .env real (H1).
os.environ.setdefault("SEC_USER_AGENT", "Test User test@example.com")

import pytest  # noqa: E402

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _network_disabled(*_args, **_kwargs):
    raise RuntimeError(
        "Real network client (LLM/web/EDGAR) constructed during tests — mock it or use a fixture "
        "(tests/CLAUDE.md: zero real API calls)."
    )


@pytest.fixture(autouse=True)
def block_real_network_clients():
    """
    Impide construir clientes reales de OpenAI, Anthropic, Tavily, EDGAR, Finnhub y Polygon en cualquier test (H1, EC-93).

    Antes de H1, varios tests hacían llamadas de red reales cuando había un .env con claves
    (y fallaban sin él). Un test que necesite el constructor real lo parchea él mismo
    (p.ej. tests/test_llm_client.py), lo que sobrescribe este bloqueo dentro de su ámbito.
    """
    with unittest.mock.patch("openai.OpenAI", _network_disabled), \
            unittest.mock.patch("anthropic.Anthropic", _network_disabled), \
            unittest.mock.patch("tavily.TavilyClient", _network_disabled),             unittest.mock.patch("edgar.Company", _network_disabled),             unittest.mock.patch("finnhub.Client", _network_disabled),             unittest.mock.patch("polygon.RESTClient", _network_disabled):
        # edgar.Company (EC-93): los tests de management descargaban filings reales de EDGAR sin
        # que nadie lo notara (el adaptador degrada en silencio). Los tests de adaptadores que lo
        # necesitan ya parchean edgar.Company ellos mismos, lo que sobrescribe este bloqueo.
        # El singleton de get_client() puede haber cacheado un cliente SDK real de un test
        # anterior: lo vaciamos para que el bloqueo aplique también a él.
        from due_diligence.llm import client as _llm_client
        if _llm_client._client is not None:
            _llm_client._client._openai = None
            _llm_client._client._anthropic = None
        yield

# Los 5 arquetipos de MCE-25. Cada test que use `archetype_raw` corre 5 veces.
# Añadir un ticker aquí + su fixture JSON es suficiente para incorporar un arquetipo nuevo.
_ARCHETYPE_TICKERS = ["aapl", "bbw", "azo", "prdo", "calm"]  # calm: cíclica (MCE-25, H1+)


@pytest.fixture(params=_ARCHETYPE_TICKERS, ids=_ARCHETYPE_TICKERS)
def archetype_raw(request):
    """
    Fixture parametrizado sobre los 5 arquetipos de XBRL (MCE-25).

    Cada test que lo declare se ejecuta 4 veces — una por arquetipo.
    Esto garantiza que los paths críticos del parser no son AAPL-specific.

    Arquetipos:
        aapl — large cap ideal, baseline
        bbw  — small cap, revenue en millones (EC-14: fmt_usd)
        azo  — equity negativo por buybacks (EC-18: IC fallback)
        prdo — asset-light, capex ausente en XBRL (FCF con capex=None)
    """
    fixture_file = _FIXTURES_DIR / f"{request.param}_xbrl_raw.json"
    return json.loads(fixture_file.read_text())


@pytest.fixture(autouse=True, scope="session")
def set_test_env():
    """Set required env vars for all tests so Settings() can instantiate."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key")
    os.environ.setdefault("SEC_USER_AGENT", "Test User test@example.com")


@pytest.fixture(autouse=True)
def mock_tavily_quota_check():
    """
    Evita llamadas HTTP reales a Tavily /usage en todos los tests.

    check_tavily_quota() hace un GET a api.tavily.com — sin este mock,
    cualquier test que invoque supervisor_node con TAVILY_API_KEY configurada
    haría una llamada de red real. Los tests que prueban el comportamiento de
    quota (TavilyQuotaExceededError) sobreescriben este mock explícitamente.
    """
    with unittest.mock.patch("due_diligence.tools.tavily.check_tavily_quota"):
        yield


def pytest_configure(config):
    """Registra el marker que permite a un test ejercitar fetch_quarterly_snapshot real."""
    config.addinivalue_line(
        "markers",
        "real_quarterly_fetch: no mockear EdgarAdapter.fetch_quarterly_snapshot (el test mockea edgar.Company)",
    )
    config.addinivalue_line(
        "markers",
        "real_8k_fetch: no mockear EdgarEventsAdapter.fetch_8k_events (el test mockea edgar.Company)",
    )
    config.addinivalue_line(
        "markers",
        "real_finnhub_fetch: no mockear FinnhubAdapter.fetch_transcripts/fetch_quarterly_earnings (el test mockea finnhub.Client)",
    )


@pytest.fixture(autouse=True)
def no_live_def14a_html():
    """EC-93: el HTML del proxy se descarga de EDGAR. Por defecto devuelve "" (sin tabla) para que
    ningún test toque la red; los que necesitan la tabla parchean con un fixture
    tests/fixtures/<ticker>_def14a_ownership.html.

    unittest.mock.patch y NO `mocker`: un fixture autouse que pide `mocker` lo instancia antes
    que los demás autouse; al deshacer en orden inverso, mocker restauraba los mocks por defecto
    de otros fixtures en vez de la función original y se filtraban entre tests (EC-93).
    """
    with unittest.mock.patch(
        "due_diligence.tools.edgar.EdgarManagementAdapter.fetch_def14a_html", return_value=("", None)
    ):
        yield


@pytest.fixture(autouse=True)
def no_live_8k_events(request):
    """
    Evita descargas reales de 8-K desde market_agent, management_agent y los tests de grafo (EC-93).

    Hasta EC-93 nadie lo mockeaba: los tests descargaban filings reales de EDGAR (2 s por test,
    suite de 38 s) y seguían pasando con la red cortada porque el adaptador degrada a [].
    Los tests de fetch_8k_events en sí (mockeando edgar.Company) se marcan con
    @pytest.mark.real_8k_fetch.
    """
    if request.node.get_closest_marker("real_8k_fetch"):
        yield
        return
    with unittest.mock.patch(
        "due_diligence.tools.edgar.EdgarEventsAdapter.fetch_8k_events", return_value=[]
    ):
        yield


@pytest.fixture(autouse=True)
def no_live_finnhub(request):
    """
    Evita llamadas reales a Finnhub (transcripts y resultados trimestrales) desde los agentes (EC-93).

    Con FINNHUB_API_KEY en .env, market_agent y management_agent llamaban a la API real en los
    tests. Los tests del adaptador (tests/test_finnhub_transcripts.py) se marcan con
    @pytest.mark.real_finnhub_fetch y mockean finnhub.Client ellos mismos.
    """
    if request.node.get_closest_marker("real_finnhub_fetch"):
        yield
        return
    with unittest.mock.patch("due_diligence.tools.finnhub.FinnhubAdapter.fetch_transcripts", return_value=[]),             unittest.mock.patch("due_diligence.tools.finnhub.FinnhubAdapter.fetch_quarterly_earnings",
                                return_value=[]):
        yield


@pytest.fixture(autouse=True)
def no_live_quarterly_snapshot(request):
    """
    Evita llamadas de red reales a EDGAR desde financials_node (EC-84, T7).

    Hasta T7 el snapshot trimestral nunca se producía (bug de formato), así que los
    tests que corrían financials_node con fetch_raw mockeado hacían una llamada real
    a EDGAR que acababa en None sin que nadie lo notara. Con el snapshot arreglado esa
    llamada devolvería datos vivos → tests no deterministas. Los tests que prueban
    fetch_quarterly_snapshot en sí (mockeando edgar.Company) se marcan con
    @pytest.mark.real_quarterly_fetch.
    """
    if request.node.get_closest_marker("real_quarterly_fetch"):
        yield
        return
    with unittest.mock.patch(
        "due_diligence.tools.edgar.EdgarAdapter.fetch_quarterly_snapshot", return_value=None
    ):
        yield
