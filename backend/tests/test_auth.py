import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import flowsage_backend.api.auth as auth_module
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


async def test_unknown_email_still_runs_a_password_verify(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: the unknown-email path must cost about the same as a real
    failed login (both call verify_password once), or response timing leaks which
    emails have accounts."""
    calls: list[tuple[str, str]] = []
    real_verify_password = auth_module.verify_password

    def spy_verify_password(password: str, hashed: str) -> bool:
        calls.append((password, hashed))
        return real_verify_password(password, hashed)

    monkeypatch.setattr(auth_module, "verify_password", spy_verify_password)

    async with await _client(app) as client:
        response = await client.post(
            "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )

    assert response.status_code == 401
    assert len(calls) == 1
    assert calls[0] == ("whatever", auth_module.dummy_password_hash())


async def test_logout_cookie_attributes_match_login_cookie(app: FastAPI) -> None:
    async with await _client(app) as client:
        response = await client.post("/auth/logout")

    set_cookie = response.headers.get("set-cookie", "")
    assert "flowsage_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


async def test_switch_workspace_rejects_non_member(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "switch-reject@example.com", "hunter2")
    async with await _client(app) as client:
        await client.post(
            "/auth/login", json={"email": "switch-reject@example.com", "password": "hunter2"}
        )
        response = await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(uuid.uuid4())}
        )

    assert response.status_code == 403


async def test_switch_workspace_succeeds_for_member(app: FastAPI, db_session: AsyncSession) -> None:
    from flowsage_backend.models.workspace import Membership, Role, Workspace

    user = await upsert_user(db_session, "switch-ok@example.com", "hunter2")
    second_workspace = Workspace(name="Second", slug=f"fs-{uuid.uuid4().hex[:8]}")
    db_session.add(second_workspace)
    await db_session.flush()
    db_session.add(Membership(user_id=user.id, workspace_id=second_workspace.id, role=Role.VIEWER))
    await db_session.commit()

    async with await _client(app) as client:
        await client.post(
            "/auth/login", json={"email": "switch-ok@example.com", "password": "hunter2"}
        )
        response = await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(second_workspace.id)}
        )

    assert response.status_code == 200
    assert response.json()["workspace_id"] == str(second_workspace.id)
    assert response.json()["role"] == "viewer"
