from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def test_slack_integration_starts_disconnected(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-slack-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        response = await client.get("/settings/integrations/slack")

    assert response.status_code == 200
    assert response.json() == {"connected": False, "webhook_url_preview": None}


async def test_connect_and_disconnect_slack(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-slack-connect-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        connect = await client.put(
            "/settings/integrations/slack", json={"webhook_url": "https://hooks.slack.test/abc"}
        )
        assert connect.status_code == 200
        assert connect.json()["connected"] is True

        disconnect = await client.delete("/settings/integrations/slack")
        assert disconnect.status_code == 204

        status_response = await client.get("/settings/integrations/slack")
        assert status_response.json()["connected"] is False


async def test_connect_and_disconnect_jira(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-jira-connect-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        connect = await client.put(
            "/settings/integrations/jira",
            json={
                "base_url": "https://acme.atlassian.net",
                "email": "bot@acme.test",
                "api_token": "tok",
                "project_key": "FS",
            },
        )
        assert connect.status_code == 200
        assert connect.json() == {
            "connected": True,
            "base_url": "https://acme.atlassian.net",
            "email": "bot@acme.test",
            "project_key": "FS",
        }

        disconnect = await client.delete("/settings/integrations/jira")
        assert disconnect.status_code == 204

        status_response = await client.get("/settings/integrations/jira")
        assert status_response.json()["connected"] is False


async def test_create_api_key_reveals_raw_key_once(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-key-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post("/settings/integrations/api-keys", json={"name": "CI"})
        assert create.status_code == 201
        body = create.json()
        assert body["key"].startswith("fs_live_")

        listing = await client.get("/settings/integrations/api-keys")
        assert listing.status_code == 200
        assert "key" not in listing.json()[0]
        assert listing.json()[0]["key_prefix"] == body["key_prefix"]


async def test_revoke_api_key(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-key-revoke-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post("/settings/integrations/api-keys", json={"name": "CI"})
        key_id = create.json()["id"]

        revoke = await client.delete(f"/settings/integrations/api-keys/{key_id}")
        assert revoke.status_code == 204

        listing = await client.get("/settings/integrations/api-keys")
        assert listing.json()[0]["revoked"] is True


async def test_create_webhook_reveals_secret_once_and_lists_it_without_secret(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"int-webhook-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/hook", "event_types": ["alert.triggered"]},
        )
        assert create.status_code == 201
        assert "secret" in create.json()

        listing = await client.get("/settings/integrations/webhooks")
        assert "secret" not in listing.json()[0]


async def test_update_and_delete_webhook(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-webhook-update-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/hook", "event_types": ["alert.triggered"]},
        )
        webhook_id = create.json()["id"]

        update = await client.patch(
            f"/settings/integrations/webhooks/{webhook_id}", json={"enabled": False}
        )
        assert update.status_code == 200
        assert update.json()["enabled"] is False

        delete = await client.delete(f"/settings/integrations/webhooks/{webhook_id}")
        assert delete.status_code == 204

        listing = await client.get("/settings/integrations/webhooks")
        assert listing.json() == []


async def test_test_webhook_endpoint_records_a_delivery(app: FastAPI, db_session: AsyncSession) -> None:
    import respx
    from httpx import Response

    email = f"int-webhook-test-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/hook", "event_types": ["alert.triggered"]},
        )
        webhook_id = create.json()["id"]

        with respx.mock:
            respx.post("https://example.test/hook").mock(return_value=Response(200))
            test_response = await client.post(f"/settings/integrations/webhooks/{webhook_id}/test")
        assert test_response.status_code == 200
        assert test_response.json()["success"] is True

        deliveries = await client.get(f"/settings/integrations/webhooks/{webhook_id}/deliveries")
        assert len(deliveries.json()) == 1
        assert deliveries.json()[0]["event_type"] == "test"


async def test_viewer_cannot_create_api_key(app: FastAPI, db_session: AsyncSession) -> None:
    """Mutating endpoints require Role.ADMIN -- a fresh user is ADMIN of their own
    workspace (see `seed.upsert_user`), so this test demotes them first."""
    from sqlalchemy import select

    from flowsage_backend.models.workspace import Membership, Role

    email = f"int-viewer-{uuid.uuid4().hex[:8]}@example.com"
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    membership.role = Role.VIEWER
    await db_session.commit()

    async with _authed_client(app, email) as client:
        response = await client.post("/settings/integrations/api-keys", json={"name": "CI"})

    assert response.status_code == 403
