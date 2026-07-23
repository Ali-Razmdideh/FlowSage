"""Verifies the workspace/membership schema migration + default-workspace backfill
against a real ephemeral Postgres (not mocked): pre-existing rows created before
this chunk's migrations must land in a single auto-created "Default" workspace,
with `workspace_id` non-nullable afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from testcontainers.postgres import PostgresContainer

from flowsage_backend.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    # migrations/env.py always builds an async engine (asyncpg), so use that driver
    # here too rather than testcontainers' default psycopg2 URL (not a project dep).
    with PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url().replace("psycopg2", "asyncpg")


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_backfill_migration_creates_default_workspace_and_scopes_existing_rows(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    # migrations/env.py resolves the connection via flowsage_backend.config.get_settings()
    # rather than the Alembic Config object passed to command.upgrade() -- point that at
    # the ephemeral container instead of the default local-dev Postgres.
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)

    config = _alembic_config(postgres_url)
    # Migrate to just before this chunk's changes, seed pre-existing rows, then upgrade.
    command.upgrade(config, "1c165b4afcfa")

    user_id = uuid.uuid4()
    persona_id = uuid.uuid4()

    async def _seed() -> None:
        engine = create_async_engine(postgres_url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, created_at) "
                    "VALUES (:id, 'pre-existing@example.com', 'x', now())"
                ),
                {"id": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO personas (id, slug, name, description, baseline, tech_affinity, "
                    "primary_device, discovery_mode, contextual_triggers, technical_literacy, "
                    "anxiety, patience, curiosity, model, created_at) VALUES "
                    "(:id, 'pre-existing', 'Pre-existing', 'd', false, 'Low', 'Desktop', 'Search', "
                    "'[]', 0.5, 0.5, 0.5, 0.5, 'claude-sonnet-4-5', now())"
                ),
                {"id": persona_id},
            )
        await engine.dispose()

    asyncio.run(_seed())

    command.upgrade(config, "head")

    async def _verify() -> tuple[uuid.UUID, str, uuid.UUID, str]:
        engine = create_async_engine(postgres_url)
        async with engine.begin() as conn:
            default_workspace_id = (
                await conn.execute(text("SELECT id FROM workspaces WHERE slug = 'fs-default'"))
            ).scalar_one()
            membership_role = (
                await conn.execute(
                    text("SELECT role FROM memberships WHERE user_id = :uid"), {"uid": user_id}
                )
            ).scalar_one()
            persona_workspace_id = (
                await conn.execute(
                    text("SELECT workspace_id FROM personas WHERE id = :pid"), {"pid": persona_id}
                )
            ).scalar_one()
            # workspace_id is NOT NULL after the backfill.
            is_nullable = (
                await conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'personas' AND column_name = 'workspace_id'"
                    )
                )
            ).scalar_one()
        await engine.dispose()
        return default_workspace_id, membership_role, persona_workspace_id, is_nullable

    default_workspace_id, membership_role, persona_workspace_id, is_nullable = asyncio.run(
        _verify()
    )

    assert membership_role == "ADMIN"
    assert persona_workspace_id == default_workspace_id
    assert is_nullable == "NO"
