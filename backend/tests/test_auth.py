from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_login_succeeds_and_sets_cookie(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "login-ok@example.com", "hunter2")

    async with await _client(app) as client:
        response = await client.post(
            "/auth/login", json={"email": "login-ok@example.com", "password": "hunter2"}
        )

    assert response.status_code == 200
    assert response.json()["email"] == "login-ok@example.com"
    assert "flowsage_session" in response.cookies


async def test_login_rejects_wrong_password(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "wrong-pw@example.com", "hunter2")

    async with await _client(app) as client:
        response = await client.post(
            "/auth/login", json={"email": "wrong-pw@example.com", "password": "nope"}
        )

    assert response.status_code == 401


async def test_login_rejects_unknown_email(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post(
            "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )

    assert response.status_code == 401


async def test_me_requires_authentication(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_me_returns_current_user_after_login(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "me-flow@example.com", "hunter2")

    async with await _client(app) as client:
        await client.post(
            "/auth/login", json={"email": "me-flow@example.com", "password": "hunter2"}
        )
        response = await client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "me-flow@example.com"


async def test_logout_clears_session(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "logout-flow@example.com", "hunter2")

    async with await _client(app) as client:
        await client.post(
            "/auth/login", json={"email": "logout-flow@example.com", "password": "hunter2"}
        )
        await client.post("/auth/logout")
        response = await client.get("/auth/me")

    assert response.status_code == 401


async def test_me_rejects_tampered_cookie(app: FastAPI) -> None:
    async with await _client(app) as client:
        client.cookies.set("flowsage_session", "not-a-real-jwt")
        response = await client.get("/auth/me")

    assert response.status_code == 401
