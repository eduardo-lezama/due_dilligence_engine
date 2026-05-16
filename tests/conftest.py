import json
import os
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
