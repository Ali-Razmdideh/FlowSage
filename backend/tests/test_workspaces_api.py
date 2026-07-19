"""backend/tests/test_workspaces_api.py"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(
    app: FastAPI, db_session: AsyncSession, email: str
) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, email, "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def test_get_current_workspace(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(
        app, db_session, f"ws-get-{uuid.uuid4().hex[:8]}@example.com"
    ) as client:
        response = await client.get("/workspaces/current")

    assert response.status_code == 200
    assert response.json()["name"] == "Default"


async def test_update_current_workspace(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(
        app, db_session, f"ws-patch-{uuid.uuid4().hex[:8]}@example.com"
    ) as client:
        response = await client.patch(
            "/workspaces/current",
            json={
                "name": "Acme Corp",
                "description": "Our workspace",
                "avatar_url": None,
                "privacy": "restricted",
                "region": "eu",
                "retention_days": 30,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Acme Corp"
    assert body["privacy"] == "restricted"
    assert body["retention_days"] == 30


async def test_archive_current_workspace(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(
        app, db_session, f"ws-archive-{uuid.uuid4().hex[:8]}@example.com"
    ) as client:
        response = await client.post("/workspaces/current/archive")

    assert response.status_code == 200
    assert response.json()["archived"] is True


async def test_list_workspaces_shows_only_memberships(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(
        app, db_session, f"ws-list-{uuid.uuid4().hex[:8]}@example.com"
    ) as client:
        response = await client.get("/workspaces")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["role"] == "admin"


async def test_create_workspace_makes_caller_admin(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(
        app, db_session, f"ws-create-{uuid.uuid4().hex[:8]}@example.com"
    ) as client:
        create_response = await client.post("/workspaces", json={"name": "New Co"})
        assert create_response.status_code == 201

        list_response = await client.get("/workspaces")

    assert len(list_response.json()) == 2
