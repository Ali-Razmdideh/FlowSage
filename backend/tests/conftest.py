"""Shared fixtures: session-scoped ephemeral Postgres + Redis containers via testcontainers.

Tests never depend on `infra/docker-compose.yml` already being up — each test run
spins its own throwaway Postgres/Redis, so `pytest` is hermetic in CI and locally.
Tables are created once per session directly from the ORM metadata (Alembic's
upgrade path is verified separately, by hand, against a real docker-compose Postgres).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from testcontainers.neo4j import Neo4jContainer
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from flowsage_backend.config import Settings
from flowsage_backend.main import create_app
from flowsage_backend.models import Base
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.seed import upsert_user


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as container:
        port = container.get_exposed_port(6379)
        yield f"redis://{container.get_container_host_ip()}:{port}/0"


@pytest.fixture(scope="session")
async def _tables_ready(postgres_url: str) -> AsyncIterator[None]:
    engine: AsyncEngine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture
def settings(postgres_url: str, redis_url: str, tmp_path: Path) -> Settings:
    return Settings(
        database_url=postgres_url, redis_url=redis_url, upload_dir=str(tmp_path / "uploads")
    )


@pytest.fixture
async def app(settings: Settings, _tables_ready: None) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def db_session(postgres_url: str, _tables_ready: None) -> AsyncIterator[AsyncSession]:
    """A standalone DB session for tests that exercise repository/seed logic directly."""
    engine = create_async_engine(postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def second_workspace_membership(db_session: AsyncSession) -> tuple[User, Membership]:
    """A second user in a second workspace, for cross-tenant isolation tests."""
    user = await upsert_user(db_session, "other-tenant@example.com", "hunter2")
    result = await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    return user, result.scalar_one()


async def ensure_default_workspace(session: AsyncSession) -> uuid.UUID:
    """Get-or-create the "fs-default" workspace that `POST /v1/events` (API-key
    ingestion, not yet workspace-scoped -- proper per-workspace API keys are
    Phase 3 chunk 2) always writes into, and the digest cron job reads from.

    In a real deployment this row is created by the `e463496b1d0f` backfill
    migration; test fixtures build tables straight from ORM metadata (no
    migrations run), so it doesn't exist until a test needs it here."""
    result = await session.execute(select(Workspace.id).where(Workspace.slug == "fs-default"))
    workspace_id = result.scalar_one_or_none()
    if workspace_id is not None:
        return workspace_id

    workspace = Workspace(name="Default", slug="fs-default")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


async def login_to_default_workspace(
    client: AsyncClient, session: AsyncSession, email: str, password: str = "hunter2"
) -> uuid.UUID:
    """Log `email` into the shared "fs-default" workspace (joining it first if
    they're not already a member), for tests that combine `/v1/events`
    (API-key ingestion, hardcoded to "fs-default") with an authenticated-
    session query -- both must resolve to the same workspace to see each
    other's data before real per-workspace API keys land (chunk 2)."""
    user = await upsert_user(session, email, password)
    workspace_id = await ensure_default_workspace(session)

    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id, Membership.workspace_id == workspace_id
        )
    )
    if result.scalar_one_or_none() is None:
        session.add(Membership(user_id=user.id, workspace_id=workspace_id, role=Role.ADMIN))
        await session.commit()

    await client.post("/auth/login", json={"email": email, "password": password})
    await client.post("/auth/switch-workspace", json={"workspace_id": str(workspace_id)})
    return workspace_id


@pytest.fixture(scope="session")
def neo4j_credentials() -> Iterator[tuple[str, str, str]]:
    """Session-scoped and only started by tests that request it (real Neo4j
    ingestion), so the rest of the suite doesn't pay for a JVM container startup."""
    with Neo4jContainer("neo4j:5.24-community", password="flowsage_test") as container:
        yield container.get_connection_url(), "neo4j", "flowsage_test"


@pytest.fixture
async def app_with_real_neo4j(
    settings: Settings, _tables_ready: None, neo4j_credentials: tuple[str, str, str]
) -> AsyncIterator[FastAPI]:
    uri, user, password = neo4j_credentials
    application = create_app(
        settings.model_copy(
            update={"neo4j_uri": uri, "neo4j_user": user, "neo4j_password": password}
        )
    )
    async with application.router.lifespan_context(application):
        yield application
