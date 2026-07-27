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
from flowsage_backend.models.workspace import Membership, Role
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


async def _fresh_membership(
    db_session: AsyncSession, prefix: str, role: Role = Role.ADMIN
) -> tuple[str, uuid.UUID]:
    """A brand-new user with their own personal workspace at `role`. `/auth/login`
    lands on that workspace (it picks the user's oldest membership), so this is
    the cheapest way to drive the billing routes as a specific role."""
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    if membership.role is not role:
        membership.role = role
        await db_session.commit()
    return email, membership.workspace_id


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


async def test_checkout_requires_admin_role(app: FastAPI, db_session: AsyncSession) -> None:
    email, _ = await _fresh_membership(db_session, "billing-viewer-checkout", Role.VIEWER)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.post("/billing/checkout", json={"tier": "pro"})

    assert response.status_code == 403


async def test_portal_requires_admin_role(app: FastAPI, db_session: AsyncSession) -> None:
    email, _ = await _fresh_membership(db_session, "billing-viewer-portal", Role.VIEWER)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.post("/billing/portal")

    assert response.status_code == 403


async def test_usage_stays_readable_by_non_admins(app: FastAPI, db_session: AsyncSession) -> None:
    email, _ = await _fresh_membership(db_session, "billing-viewer-usage", Role.VIEWER)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.get("/billing/usage")

    assert response.status_code == 200


async def test_checkout_returns_409_when_subscription_already_active(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A Pro -> Team switch must not mint a second Stripe subscription (that
    would double-bill and orphan the old one); the caller is sent to the
    Customer Portal instead."""
    email, workspace_id = await _fresh_membership(db_session, "billing-already-subbed")
    subscription = await get_or_create_subscription(db_session, workspace_id)
    subscription.tier = SubscriptionTier.PRO
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_customer_id = "cus_already_123"
    subscription.stripe_subscription_id = "sub_already_123"
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.post("/billing/checkout", json={"tier": "team"})

    assert response.status_code == 409
    assert "Manage Billing" in response.json()["detail"]


async def test_webhook_returns_200_on_malformed_metadata(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A 5xx back at Stripe triggers retry-storm-then-auto-disable, so even a
    garbage workspace_id must come back 200."""
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"
    payload = json.dumps(
        {
            "id": "evt_test_bad_metadata",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_bad",
                    "subscription": "sub_bad",
                    "metadata": {"workspace_id": "not-a-uuid", "tier": "pro"},
                }
            },
        }
    ).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook",
            content=payload,
            headers={"stripe-signature": _sign(payload, "whsec_test_fake")},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "error"}


async def test_webhook_paused_status_does_not_downgrade_tier(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """`paused` / `incomplete` (SCA/3DS in flight) are transient -- the paid
    tier must survive them. Only `canceled`/`incomplete_expired` reset to Free."""
    _, workspace_id = await _fresh_membership(db_session, "billing-paused")
    subscription = await get_or_create_subscription(db_session, workspace_id)
    subscription.tier = SubscriptionTier.PRO
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_customer_id = "cus_paused_123"
    subscription.stripe_subscription_id = "sub_paused_123"
    await db_session.commit()
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"

    payload = json.dumps(
        {
            "id": "evt_test_paused",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_paused_123",
                    "customer": "cus_paused_123",
                    "status": "paused",
                }
            },
        }
    ).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook",
            content=payload,
            headers={"stripe-signature": _sign(payload, "whsec_test_fake")},
        )

    assert response.status_code == 200
    db_session.expire_all()
    updated = (
        await db_session.execute(
            select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
        )
    ).scalar_one()
    assert updated.tier == SubscriptionTier.PRO


async def test_webhook_canceled_status_does_downgrade_tier(
    app: FastAPI, db_session: AsyncSession
) -> None:
    _, workspace_id = await _fresh_membership(db_session, "billing-canceled")
    subscription = await get_or_create_subscription(db_session, workspace_id)
    subscription.tier = SubscriptionTier.PRO
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.stripe_customer_id = "cus_canceled_123"
    subscription.stripe_subscription_id = "sub_canceled_123"
    await db_session.commit()
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"

    payload = json.dumps(
        {
            "id": "evt_test_canceled",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_canceled_123",
                    "customer": "cus_canceled_123",
                    "status": "canceled",
                }
            },
        }
    ).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook",
            content=payload,
            headers={"stripe-signature": _sign(payload, "whsec_test_fake")},
        )

    assert response.status_code == 200
    db_session.expire_all()
    updated = (
        await db_session.execute(
            select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
        )
    ).scalar_one()
    assert updated.tier == SubscriptionTier.FREE
    assert updated.status == SubscriptionStatus.CANCELED
