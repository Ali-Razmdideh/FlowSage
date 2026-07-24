from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import record_audit_event
from tests.conftest import create_workspace_and_admin


async def test_get_audit_logs_returns_workspace_entries(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"audit-api-{uuid.uuid4().hex[:8]}@example.com"
    user, membership = await create_workspace_and_admin(db_session, email)
    await record_audit_event(
        db_session, membership.workspace_id, actor_user_id=user.id, action="auth.login"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.get("/audit-logs")

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) >= 1
    assert any(e["action"] == "auth.login" for e in body["entries"])


async def test_get_audit_logs_requires_auth(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/audit-logs")
    assert response.status_code == 401
