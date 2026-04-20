"""
Configuración centralizada del proyecto.

Qué hace este módulo:
    Define la clase Settings que carga todas las variables de entorno desde
    el archivo .env usando Pydantic Settings. Es el único lugar donde se
    definen las configuraciones del sistema.

Por qué Pydantic Settings:
    - Validación automática de tipos
    - Valores por defecto
    - Mensajes de error claros si falta una variable requerida
    - Soporte para .env files

Variables requeridas:
    - SEC_USER_AGENT: identificación para EDGAR (nombre + email)
    - ANTHROPIC_API_KEY: clave de API de Anthropic Claude

Variables opcionales:
    - OPENAI_API_KEY: fallback a GPT-4 si Anthropic falla
    - POLYGON_API_KEY: datos de mercado (opcional)
    - FINNHUB_API_KEY: datos alternativos de mercado (opcional)
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    # --- LLM Providers ---
    anthropic_api_key: str = Field("", validation_alias="ANTHROPIC_API_KEY")  # Claude API key
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY")  # GPT-4 fallback
    sec_user_agent: str = Field(..., validation_alias="SEC_USER_AGENT")  # EDGAR identificación (requerido)

    # --- Market Data APIs ---
    polygon_api_key: str = Field("", validation_alias="POLYGON_API_KEY")  # Datos de mercado
    finnhub_api_key: str = Field("", validation_alias="FINNHUB_API_KEY")  # Datos alternativos de mercado
    tavily_api_key: str = Field("", validation_alias="TAVILY_API_KEY")    # Búsqueda web para agentes (ADR-012)

    # --- Model Configuration ---
    default_model: str = Field("claude-sonnet-4-6", validation_alias="DEFAULT_MODEL")  # Modelo principal (Claude)
    openai_fallback_model: str = Field("gpt-4.1", validation_alias="OPENAI_FALLBACK_MODEL")  # Fallback general (management, filings, competitive)
    openai_expert_model: str = Field("gpt-5.4-mini", validation_alias="OPENAI_EXPERT_MODEL")    # Modelo para value_expert y critic

    # --- Paths ---
    db_path: str = Field("data/analyses.db", validation_alias="DB_PATH")  # SQLite database
    reports_dir: str = Field("data/reports", validation_alias="REPORTS_DIR")  # Directorio de reportes


settings = Settings()
