"""Application configuration, loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://flowsage:flowsage_dev@localhost:5432/flowsage"
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
