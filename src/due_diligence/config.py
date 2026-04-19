from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    anthropic_api_key: str = Field(..., validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY")
    sec_user_agent: str = Field(..., validation_alias="SEC_USER_AGENT")

    polygon_api_key: str = Field("", validation_alias="POLYGON_API_KEY")
    finnhub_api_key: str = Field("", validation_alias="FINNHUB_API_KEY")

    default_model: str = Field("claude-sonnet-4-6", validation_alias="DEFAULT_MODEL")
    openai_fallback_model: str = Field("gpt-4o-mini", validation_alias="OPENAI_FALLBACK_MODEL")

    db_path: str = Field("data/analyses.db", validation_alias="DB_PATH")
    reports_dir: str = Field("data/reports", validation_alias="REPORTS_DIR")


settings = Settings()
