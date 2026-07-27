# backend/tests/test_billing_api.py
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import (
    SubscriptionStatus,
    SubscriptionTier,
    WorkspaceSubscription,
)
from flowsage_backend.models.workspace import Membership
from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "billing-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "billing-api@example.com", "password": "hunter2"}
        )
        yield client


async def _billing_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    user = await upsert_user(db_session, "billing-api@example.com", "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def test_get_usage_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/billing/usage")
    assert response.status_code == 401


async def test_get_usage_returns_free_tier_defaults(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.get("/billing/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["seats_limit"] == 1


async def test_checkout_returns_400_when_stripe_unconfigured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/billing/checkout", json={"tier": "pro"})

    assert response.status_code == 400


async def test_portal_returns_400_when_no_stripe_customer_yet(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/billing/portal")

    assert response.status_code == 400


async def test_webhook_rejects_bad_signature(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook",
            content=b'{"type": "checkout.session.completed"}',
            headers={"stripe-signature": "t=1,v1=deadbeef"},
        )

    assert response.status_code == 400


def _sign(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


async def test_webhook_checkout_completed_upgrades_tier(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _billing_api_workspace_id(db_session)
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"

    payload = json.dumps(
        {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test_123",
                    "subscription": "sub_test_123",
                    "metadata": {"workspace_id": str(workspace_id), "tier": "pro"},
                }
            },
        }
    ).encode()
    sig_header = _sign(payload, "whsec_test_fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook", content=payload, headers={"stripe-signature": sig_header}
        )

    assert response.status_code == 200
    result = await db_session.execute(
        select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
    )
    subscription = result.scalar_one()
    assert subscription.tier == SubscriptionTier.PRO
    assert subscription.stripe_customer_id == "cus_test_123"
    assert subscription.stripe_subscription_id == "sub_test_123"


async def test_webhook_subscription_deleted_resets_to_free(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _billing_api_workspace_id(db_session)
    # Use get_or_create rather than a bare `WorkspaceSubscription(...)` insert:
    # this test file reuses the same upserted user/workspace across every test
    # in the module (see `upsert_user`'s idempotent-by-email lookup), and an
    # earlier test in this file may have already lazily created this
    # workspace's subscription row (e.g. via GET /billing/usage or the
    # checkout-completed webhook test). Inserting a second row with the same
    # workspace_id would violate the unique index on `workspace_id`.
    subscription = await get_or_create_subscription(db_session, workspace_id)
    subscription.tier = SubscriptionTier.PRO
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_customer_id = "cus_test_123"
    subscription.stripe_subscription_id = "sub_test_123"
    await db_session.commit()
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"

    payload = json.dumps(
        {
            "id": "evt_test_2",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_test_123", "customer": "cus_test_123"}},
        }
    ).encode()
    sig_header = _sign(payload, "whsec_test_fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook", content=payload, headers={"stripe-signature": sig_header}
        )

    assert response.status_code == 200
    # The webhook was processed through a *different* AsyncSession (the app's
    # own `get_db_session`-provided one), which committed the update to
    # Postgres. `db_session` here still has the pre-webhook `subscription` row
    # cached in its identity map from the `get_or_create_subscription` call
    # above, and plain re-`select()`s don't refresh already-identity-mapped
    # rows -- so expire it first to force a real re-fetch of the committed
    # state (same pattern as this suite's existing `db_session.refresh(...)`
    # calls elsewhere, just applied session-wide since we don't hold a
    # reference to the specific row instance at this point).
    db_session.expire_all()
    result = await db_session.execute(
        select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
    )
    subscription = result.scalar_one()
    assert subscription.tier == SubscriptionTier.FREE
    assert subscription.status == SubscriptionStatus.CANCELED
