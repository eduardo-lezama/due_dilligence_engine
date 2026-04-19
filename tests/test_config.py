import importlib
import pytest
from pydantic import ValidationError


def test_settings_loads_required_fields(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")

    import due_diligence.config as cfg_module
    importlib.reload(cfg_module)

    assert cfg_module.settings.anthropic_api_key == "sk-test-key"
    assert cfg_module.settings.sec_user_agent == "Test User test@example.com"
    assert cfg_module.settings.default_model == "claude-sonnet-4-6"
    assert cfg_module.settings.openai_fallback_model == "gpt-4o-mini"


def test_settings_missing_required_field_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    import due_diligence.config as cfg_module

    with pytest.raises((ValidationError, Exception)):
        importlib.reload(cfg_module)
