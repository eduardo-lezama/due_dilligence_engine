import json
import os
import unittest.mock
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Los 4 arquetipos de MCE-25. Cada test que use `archetype_raw` corre 4 veces.
# Añadir un ticker aquí + su fixture JSON es suficiente para incorporar un arquetipo nuevo.
_ARCHETYPE_TICKERS = ["aapl", "bbw", "azo", "prdo"]


@pytest.fixture(params=_ARCHETYPE_TICKERS, ids=_ARCHETYPE_TICKERS)
def archetype_raw(request):
    """
    Fixture parametrizado sobre los 4 arquetipos de XBRL (MCE-25).

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
