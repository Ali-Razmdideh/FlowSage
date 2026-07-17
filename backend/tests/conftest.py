"""Shared fixtures: a session-scoped ephemeral Postgres container via testcontainers.

Tests never depend on `infra/docker-compose.yml` already being up — each test run
spins its own throwaway Postgres, so `pytest` is hermetic in CI and locally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from testcontainers.postgres import PostgresContainer

from flowsage_backend.config import Settings
from flowsage_backend.main import create_app


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
def settings(postgres_url: str) -> Settings:
    return Settings(database_url=postgres_url)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application
