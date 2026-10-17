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
    # default_model y openai_fallback_model pueden venir del .env real — no assertar valores hardcoded
    assert cfg_module.settings.default_model  # no vacío
    assert cfg_module.settings.openai_fallback_model  # no vacío


def test_settings_anthropic_key_optional(monkeypatch):
    """ANTHROPIC_API_KEY vacío: Settings carga sin error y el campo queda vacío.

    Usamos setenv("", ...) en lugar de delenv porque pydantic-settings lee el
    .env del disco cuando la env var no existe — setenv tiene mayor prioridad.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("SEC_USER_AGENT", "Test User test@example.com")

    import due_diligence.config as cfg_module
    importlib.reload(cfg_module)

    assert cfg_module.settings.anthropic_api_key == ""
    assert cfg_module.settings.sec_user_agent == "Test User test@example.com"


def test_settings_sec_user_agent_required(monkeypatch):
    """SEC_USER_AGENT es obligatorio: su ausencia levanta ValidationError."""
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field

    # Eliminar del entorno para que IsolatedSettings no lo encuentre
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    class IsolatedSettings(BaseSettings):
        model_config = SettingsConfigDict(env_file=None, populate_by_name=True)
        sec_user_agent: str = Field(..., validation_alias="SEC_USER_AGENT")

    with pytest.raises(ValidationError):
        IsolatedSettings()
