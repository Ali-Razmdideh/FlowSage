"""Shared fixtures: a session-scoped ephemeral Postgres container via testcontainers.

Tests never depend on `infra/docker-compose.yml` already being up — each test run
spins its own throwaway Postgres, so `pytest` is hermetic in CI and locally. Tables
are created once per session directly from the ORM metadata (Alembic's upgrade path
is verified separately, by hand, against a real docker-compose Postgres).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from testcontainers.postgres import PostgresContainer

from flowsage_backend.config import Settings
from flowsage_backend.main import create_app
from flowsage_backend.models import Base


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture(scope="session")
async def _tables_ready(postgres_url: str) -> AsyncIterator[None]:
    engine: AsyncEngine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture
def settings(postgres_url: str) -> Settings:
    return Settings(database_url=postgres_url)


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
