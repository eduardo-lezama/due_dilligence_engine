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
    # Modelo cheap para validación post-hoc de thesis-break en value_expert (ADR-031).
    # Solo se invoca cuando el Python word-boundary trigger dispara — evento raro.
    # gpt-4o-mini es suficiente para clasificación binaria "condición futura vs hecho actual".
    openai_validation_model: str = Field(
        "gpt-4o-mini", validation_alias="OPENAI_VALIDATION_MODEL",
    )

    # --- Feature tuning ---
    # Número de earnings call transcripts a inyectar en management_agent (SOURCE 5).
    # 2 quarters captura el tono actual del management con la mitad de tokens vs 4.
    # Aumentar a 4 si quieres contexto histórico extra (coste ~2x en SOURCE 5).
    transcript_quarters: int = Field(2, validation_alias="TRANSCRIPT_QUARTERS")

    # Ventana de búsqueda de 8-K materiales (ADR-017).
    # 12 meses captura el año fiscal completo — restatements (4.02) y cambios de
    # management (5.02) siguen siendo relevantes aunque ocurrieran hace 9-10 meses.
    events_lookback_months: int = Field(12, validation_alias="EVENTS_LOOKBACK_MONTHS")

    # News coverage extendida (MCE-19) — complementa Finnhub con Tavily targeting.
    # Tavily se activa con queries específicas cuando el stock está en drawdown (catalizador
    # típico fuera de la ventana de 30 días de Finnhub). Sin esto, news flow pierde
    # contexto crítico (tariff disclosures, profit warnings, guidance cuts antiguos).
    market_news_include_tavily: bool = Field(True, validation_alias="MARKET_NEWS_INCLUDE_TAVILY")
    # Threshold de drawdown que activa búsqueda Tavily extendida (sin filtro temporal):
    # ratio price_current / price_52w_high < threshold → ampliar cobertura.
    market_news_drawdown_threshold: float = Field(
        0.70, validation_alias="MARKET_NEWS_DRAWDOWN_THRESHOLD",
    )

    # 8-K Items a filtrar (MCE-20). Default ampliado para capturar más eventos materiales:
    # - 1.01: Material Definitive Agreement (M&A, contratos relevantes)
    # - 2.02: Results of Operations (earnings)
    # - 4.02: Non-Reliance on Previously Issued Financials (restatement) — RED FLAG crítica
    # - 5.02: Departure/Appointment of Officers
    # - 7.01: Reg FD Disclosure (guidance updates fuera de earnings)
    # - 8.01: Other Events (anuncios materiales — incluye tariff disclosures, supply chain)
    # Configurable como lista separada por comas en .env: EDGAR_8K_ITEMS=2.02,4.02,5.02
    edgar_8k_items: str = Field(
        "1.01,2.02,4.02,5.02,7.01,8.01",
        validation_alias="EDGAR_8K_ITEMS",
    )

    # --- Performance tracking (ADR-021) ---
    # Tasa de cambio USD→EUR para reportar costes de LLM en EUR.
    # Usar USD_EUR_RATE en .env para actualizar sin tocar código.
    usd_eur_rate: float = Field(0.93, validation_alias="USD_EUR_RATE")

    # --- Paths ---
    db_path: str = Field("data/analyses.db", validation_alias="DB_PATH")  # SQLite database
    reports_dir: str = Field("data/reports", validation_alias="REPORTS_DIR")  # Directorio de reportes


settings = Settings()
