"""Application configuration, loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://flowsage:flowsage_dev@localhost:5432/flowsage"
    environment: str = "development"

    # Single-tenant JWT session (Phase 1: one seeded user, no public signup).
    # jwt_secret has no safe default for anything but local dev -- override via env in
    # any shared/deployed environment.
    # >= 32 bytes: PyJWT warns below that for HS256 (RFC 7518 section 3.2).
    jwt_secret: str = "dev-secret-change-me-before-deploying-32bytes"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7  # 1 week
    cookie_name: str = "flowsage_session"
    cookie_secure: bool = False  # set True once served over HTTPS


@lru_cache
def get_settings() -> Settings:
    return Settings()
