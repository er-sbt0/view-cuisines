"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "claude"
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    database_path: str = "data/recipes.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
