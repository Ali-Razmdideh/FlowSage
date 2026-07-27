# backend/src/flowsage_backend/api/billing.py
"""Billing endpoints: usage snapshot, Stripe Checkout/Portal redirects, and the
Stripe webhook that keeps `WorkspaceSubscription` in sync.

`GET /billing/usage` is readable by any member (it's a read-only snapshot);
`POST /billing/checkout` and `POST /billing/portal` spend real money / expose a
Stripe-hosted management surface, so they require `Role.ADMIN` exactly like every
other mutating workspace-settings endpoint (see `api/workspaces.py::add_member`).

The webhook route has no auth dependency -- Stripe calls it directly -- and
always returns 200 on a recognized-but-irrelevant event, a workspace lookup
miss, *or an unexpected exception while processing* (never lets a downstream bug
surface as a 5xx that triggers Stripe's retry-storm-then-auto-disable
behavior); it 400s only on signature failure or missing webhook-secret config,
mirroring `record_audit_event`'s best-effort spirit."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import stripe

from flowsage_backend.audit import record_audit_event
from flowsage_backend.billing import UsageSnapshot, get_usage
from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.deps import get_current_membership, get_db_session, require_role
from flowsage_backend.integrations.stripe_client import (
    StripeNotConfiguredError,
    create_checkout_session,
    create_portal_session,
    verify_webhook,
)
from flowsage_backend.models.billing import (
    SubscriptionStatus,
    SubscriptionTier,
    WorkspaceSubscription,
)
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    tier: Literal["pro", "team"]


class CheckoutResult(BaseModel):
    url: str


class PortalResult(BaseModel):
    url: str


_STRIPE_STATUS_MAP: dict[str, SubscriptionStatus] = {
    "active": SubscriptionStatus.ACTIVE,
    "trialing": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "unpaid": SubscriptionStatus.PAST_DUE,
}


# The only Stripe subscription statuses that mean "this workspace has genuinely
# lost its paid plan". Everything else unrecognized (`incomplete` while a card
# is going through SCA/3DS, `paused` while collection is paused) is a
# *transient* state: `_map_stripe_status` still falls back to CANCELED for the
# `status` column, but the workspace's `tier` must NOT be reset to Free, or a
# customer mid-3DS-challenge gets silently downgraded and locked out.
_TERMINAL_STRIPE_STATUSES = frozenset({"canceled", "incomplete_expired"})


def _map_stripe_status(stripe_status: str) -> SubscriptionStatus:
    return _STRIPE_STATUS_MAP.get(stripe_status, SubscriptionStatus.CANCELED)


@router.get("/usage", response_model=UsageSnapshot, dependencies=[Depends(get_current_membership)])
async def get_billing_usage(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> UsageSnapshot:
    _, membership = membership_pair
    return await get_usage(session, membership.workspace_id)


@router.post("/checkout", response_model=CheckoutResult)
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> CheckoutResult:
    user, membership = membership_pair
    settings = request.app.state.settings
    price_id = (
        settings.stripe_price_id_pro if payload.tier == "pro" else settings.stripe_price_id_team
    )

    subscription = await get_or_create_subscription(session, membership.workspace_id)

    # A Checkout Session in mode="subscription" always *creates* a new Stripe
    # subscription -- it never modifies an existing one. Running it for a
    # workspace that already has a live subscription (e.g. a Pro -> Team
    # upgrade) would double-bill the customer and orphan the old subscription
    # id. Plan switches belong in the Stripe Customer Portal, which handles
    # proration correctly, so send the caller there instead.
    if (
        subscription.stripe_subscription_id is not None
        and subscription.status == SubscriptionStatus.ACTIVE
    ):
        raise HTTPException(
            409,
            "You already have an active subscription. Use 'Manage Billing' to change your plan.",
        )

    base_url = settings.app_base_url

    try:
        url = await create_checkout_session(
            secret_key=settings.stripe_secret_key,
            price_id=price_id,
            customer_email=user.email,
            existing_customer_id=subscription.stripe_customer_id,
            workspace_id=membership.workspace_id,
            tier=payload.tier,
            success_url=f"{base_url}/settings/billing?checkout=success",
            cancel_url=f"{base_url}/settings/billing?checkout=cancel",
        )
    except StripeNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc

    return CheckoutResult(url=url)


@router.post("/portal", response_model=PortalResult)
async def create_portal(
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> PortalResult:
    _, membership = membership_pair
    settings = request.app.state.settings
    subscription = await get_or_create_subscription(session, membership.workspace_id)

    if subscription.stripe_customer_id is None:
        raise HTTPException(400, "No billing account yet -- upgrade first")

    try:
        url = await create_portal_session(
            secret_key=settings.stripe_secret_key,
            customer_id=subscription.stripe_customer_id,
            return_url=f"{settings.app_base_url}/settings/billing",
        )
    except StripeNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc

    return PortalResult(url=url)


async def _find_subscription_by_customer_id(
    session: AsyncSession, customer_id: str
) -> WorkspaceSubscription | None:
    result = await session.execute(
        select(WorkspaceSubscription).where(WorkspaceSubscription.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


@router.post("/webhook")
async def stripe_webhook(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    settings = request.app.state.settings
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if settings.stripe_webhook_secret is None:
        raise HTTPException(400, "Stripe webhook is not configured")

    try:
        event = verify_webhook(
            payload=payload, sig_header=sig_header, webhook_secret=settings.stripe_webhook_secret
        )
    except stripe.SignatureVerificationError as exc:
        raise HTTPException(400, f"Invalid Stripe signature: {exc}") from exc

    # Past this point the request is *proven* to come from Stripe, so any
    # failure is our bug, not an attack -- and a 5xx here makes Stripe retry
    # with backoff and eventually auto-disable the endpoint. Swallow everything
    # (malformed metadata -> ValueError from uuid.UUID/SubscriptionTier, a
    # deleted workspace -> IntegrityError, ...) into a logged 200.
    try:
        return await _process_webhook_event(session, event)
    except Exception:  # noqa: BLE001 - see above: never 5xx back at Stripe
        logger.exception("Stripe webhook processing failed (event id=%s)", event.get("id"))
        return {"status": "error"}


async def _process_webhook_event(session: AsyncSession, event: stripe.Event) -> dict[str, str]:
    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        metadata = data.get("metadata", {})
        workspace_id_str = metadata.get("workspace_id")
        tier_str = metadata.get("tier")
        if workspace_id_str is None or tier_str is None:
            logger.warning("checkout.session.completed missing workspace_id/tier metadata")
            return {"status": "ignored"}

        workspace_id = uuid.UUID(workspace_id_str)
        subscription = await get_or_create_subscription(session, workspace_id)
        subscription.tier = SubscriptionTier(tier_str)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.stripe_customer_id = data.get("customer")
        subscription.stripe_subscription_id = data.get("subscription")
        await session.commit()
        await record_audit_event(
            session,
            workspace_id,
            action="billing.checkout_completed",
            extra_data={"tier": tier_str},
        )

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        if customer_id is None:
            return {"status": "ignored"}
        updated_subscription = await _find_subscription_by_customer_id(session, customer_id)
        if updated_subscription is None:
            logger.warning("subscription.updated for unknown customer %s", customer_id)
            return {"status": "ignored"}
        raw_status = data.get("status", "")
        updated_subscription.status = _map_stripe_status(raw_status)
        # Only a genuinely dead subscription loses the paid tier. An unmapped
        # but still-live status (`incomplete` during SCA/3DS, `paused`) records
        # the conservative CANCELED status but leaves `tier` untouched, so the
        # workspace isn't silently downgraded mid-authentication.
        if raw_status in _TERMINAL_STRIPE_STATUSES:
            updated_subscription.tier = SubscriptionTier.FREE
        current_period_end = data.get("current_period_end")
        if current_period_end is not None:
            updated_subscription.current_period_end = datetime.fromtimestamp(
                current_period_end, tz=timezone.utc
            )
        await session.commit()

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id is None:
            return {"status": "ignored"}
        deleted_subscription = await _find_subscription_by_customer_id(session, customer_id)
        if deleted_subscription is None:
            return {"status": "ignored"}
        deleted_subscription.status = SubscriptionStatus.CANCELED
        deleted_subscription.tier = SubscriptionTier.FREE
        await session.commit()

    return {"status": "ok"}
