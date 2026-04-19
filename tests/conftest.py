import os
import pytest


@pytest.fixture(autouse=True, scope="session")
def set_test_env():
    """Set required env vars for all tests so Settings() can instantiate."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key")
    os.environ.setdefault("SEC_USER_AGENT", "Test User test@example.com")
