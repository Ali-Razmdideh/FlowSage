"""Application configuration, loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_JWT_SECRET = "dev-secret-change-me-before-deploying-32bytes"
_PLACEHOLDER_EVENTS_API_KEY = "dev-events-api-key-change-me-before-deploying"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://flowsage:flowsage_dev@localhost:5432/flowsage"
    environment: str = "development"

    # Single-tenant JWT session (Phase 1: one seeded user, no public signup).
    # jwt_secret has no safe default for anything but local dev -- override via env in
    # any shared/deployed environment.
    # >= 32 bytes: PyJWT warns below that for HS256 (RFC 7518 section 3.2).
    jwt_secret: str = _PLACEHOLDER_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7  # 1 week
    cookie_name: str = "flowsage_session"
    cookie_secure: bool = False  # set True once served over HTTPS

    # Simulation jobs (arq/Redis worker) and where uploaded screenshots land.
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "./data/uploads"

    # Observational engine: raw events are stored in Postgres (so the funnel/
    # friction queries below can reuse flowsage_graph's tested pure functions) and
    # best-effort mirrored into Neo4j as a temporal graph for future direct queries.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "flowsage_dev"
    # POST /v1/events is meant for server-to-server ingestion (SDKs/webhooks), so it
    # checks a shared secret via X-API-Key rather than the browser session cookie.
    events_api_key: str = _PLACEHOLDER_EVENTS_API_KEY

    @model_validator(mode="after")
    def _reject_placeholder_secret_outside_dev(self) -> "Settings":
        if self.environment == "development":
            return self
        placeholders = {
            "JWT_SECRET": self.jwt_secret == _PLACEHOLDER_JWT_SECRET,
            "EVENTS_API_KEY": self.events_api_key == _PLACEHOLDER_EVENTS_API_KEY,
        }
        still_placeholder = [name for name, is_default in placeholders.items() if is_default]
        if still_placeholder:
            raise ValueError(
                f"{', '.join(still_placeholder)} still set to the dev placeholder but "
                f"ENVIRONMENT is {self.environment!r} -- set real secrets "
                "(e.g. `openssl rand -hex 32`)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
