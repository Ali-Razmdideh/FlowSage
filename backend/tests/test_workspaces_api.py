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


async def test_add_member_by_email(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-member-admin-{uuid.uuid4().hex[:8]}@example.com"
    invitee_email = f"ws-member-invitee-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, invitee_email, "hunter2")

    async with _authed_client(app, db_session, admin_email) as client:
        response = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "researcher"}
        )

    assert response.status_code == 201
    assert response.json()["email"] == invitee_email
    assert response.json()["role"] == "researcher"


async def test_add_member_rejects_unknown_email(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(
        app, db_session, f"ws-member-404-{uuid.uuid4().hex[:8]}@example.com"
    ) as client:
        response = await client.post(
            "/workspaces/current/members",
            json={"email": "nobody-registered@example.com", "role": "viewer"},
        )

    assert response.status_code == 404


async def test_add_member_rejects_duplicate(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-member-dup-admin-{uuid.uuid4().hex[:8]}@example.com"
    invitee_email = f"ws-member-dup-invitee-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, invitee_email, "hunter2")

    async with _authed_client(app, db_session, admin_email) as client:
        first = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "viewer"}
        )
        assert first.status_code == 201
        second = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "viewer"}
        )

    assert second.status_code == 409


async def test_cannot_remove_last_admin(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-last-admin-{uuid.uuid4().hex[:8]}@example.com"
    async with _authed_client(app, db_session, admin_email) as client:
        me = await client.get("/auth/me")
        members = await client.get("/workspaces/current/members")
        own_membership_id = next(
            m["id"] for m in members.json() if m["email"] == me.json()["email"]
        )
        response = await client.delete(f"/workspaces/current/members/{own_membership_id}")

    assert response.status_code == 400


async def test_non_admin_cannot_add_member(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-role-admin-{uuid.uuid4().hex[:8]}@example.com"
    viewer_email = f"ws-role-viewer-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, viewer_email, "hunter2")

    async with _authed_client(app, db_session, admin_email) as admin_client:
        await admin_client.post(
            "/workspaces/current/members", json={"email": viewer_email, "role": "viewer"}
        )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as viewer_client:
        await viewer_client.post("/auth/login", json={"email": viewer_email, "password": "hunter2"})
        # The viewer's own workspace (their personal one from upsert_user) has no admin
        # co-member yet to add -- switch into the shared workspace first.
        me = await viewer_client.get("/auth/me")
        shared_workspace_id = next(
            w["id"] for w in me.json()["workspaces"] if w["id"] != me.json()["workspace_id"]
        )
        await viewer_client.post(
            "/auth/switch-workspace", json={"workspace_id": shared_workspace_id}
        )
        response = await viewer_client.post(
            "/workspaces/current/members",
            json={"email": f"irrelevant-{uuid.uuid4().hex[:8]}@example.com", "role": "viewer"},
        )

    assert response.status_code == 403
