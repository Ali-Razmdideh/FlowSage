from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.workspace import Membership
from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def _onboarding_workspace_id(db_session: AsyncSession, email: str) -> uuid.UUID:
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def test_onboarding_status_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/onboarding/status")

    assert response.status_code == 401


async def test_onboarding_status_all_false_for_fresh_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-api-{uuid.uuid4().hex[:8]}@example.com"
    await _onboarding_workspace_id(db_session, email)

    async with _authed_client(app, email) as client:
        response = await client.get("/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "has_api_key": False,
        "has_events": False,
        "has_completed_simulation": False,
        "has_multiple_members": False,
    }
