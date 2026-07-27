"""Application configuration, loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_JWT_SECRET = "dev-secret-change-me-before-deploying-32bytes"
_PLACEHOLDER_ENCRYPTION_KEY = "dev-encryption-key-change-me-before-deploy"


class Settings(BaseSettings):
    # env_ignore_empty: docker-compose's `${VAR:-}` interpolation passes an actual
    # empty string (not "unset") when the host has no override, which would
    # otherwise defeat the `stripe_*: str | None = None` "unconfigured" defaults
    # below and turn a clean 400 into a 500 from the Stripe SDK. Treat "" the same
    # as unset for every optional field, not just Stripe's.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

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

    # Encrypts JiraIntegration.api_token / Webhook.secret at rest (crypto.py's
    # EncryptedString). Any string works as input -- crypto.py stretches it into a
    # valid Fernet key via SHA-256, so this can be a plain passphrase the same way
    # JWT_SECRET is today, with no separate key-generation step required.
    secret_encryption_key: str = _PLACEHOLDER_ENCRYPTION_KEY

    # Simulation jobs (arq/Redis worker) and where uploaded screenshots land.
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "./data/uploads"

    # Observational engine: raw events are stored in Postgres (so the funnel/
    # friction queries below can reuse flowsage_graph's tested pure functions) and
    # best-effort mirrored into Neo4j as a temporal graph for future direct queries.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "flowsage_dev"

    # Stripe billing (optional -- unconfigured means checkout/portal 400 cleanly,
    # same as Slack/Jira; no startup placeholder-guard, unlike JWT_SECRET/
    # SECRET_ENCRYPTION_KEY, since this feature must work fully unconfigured).
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id_pro: str | None = None
    stripe_price_id_team: str | None = None
    # Absolute origin the frontend is served from -- used to build Stripe
    # Checkout's success_url/cancel_url. Defaults to the docker-compose
    # frontend service's exposed port (see infra/docker-compose.yml).
    app_base_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _reject_placeholder_secret_outside_dev(self) -> "Settings":
        if self.environment == "development":
            return self
        placeholders = {
            "JWT_SECRET": self.jwt_secret == _PLACEHOLDER_JWT_SECRET,
            "SECRET_ENCRYPTION_KEY": self.secret_encryption_key == _PLACEHOLDER_ENCRYPTION_KEY,
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
