from __future__ import annotations

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user
from tests.conftest import login_to_default_workspace


async def test_login_rate_limit_returns_429_after_threshold(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"ratelimit-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        statuses = []
        for _ in range(7):
            response = await client.post(
                "/auth/login", json={"email": email, "password": "wrong-password"}
            )
            statuses.append(response.status_code)

    assert 429 in statuses
    # Every request before the limit kicks in is a normal 401 (wrong password),
    # not something rate-limiting masks as a different error.
    assert statuses[0] == 401


async def test_non_auth_routes_are_not_rate_limited_at_auth_threshold(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A sanity check that the tight 5/minute auth limit doesn't leak onto
    other routes -- /auth/me should tolerate more than 5 calls/minute."""
    email = f"ratelimit-me-{uuid.uuid4().hex[:8]}@example.com"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login_to_default_workspace(client, db_session, email)
        statuses = [(await client.get("/auth/me")).status_code for _ in range(8)]

    assert all(s == 200 for s in statuses)
