# backend/src/flowsage_backend/api/billing.py
"""Billing endpoints: usage snapshot, Stripe Checkout/Portal redirects, and the
Stripe webhook that keeps `WorkspaceSubscription` in sync. The webhook route
has no auth dependency -- Stripe calls it directly -- and always returns 200
on a recognized-but-irrelevant event or a workspace lookup miss (never lets a
downstream bug surface as a 5xx that triggers Stripe's retry storm); it 400s
only on signature failure, mirroring `record_audit_event`'s best-effort spirit."""

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
from flowsage_backend.deps import get_current_membership, get_db_session
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
from flowsage_backend.models.workspace import Membership

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


def _map_stripe_status(stripe_status: str) -> SubscriptionStatus:
    return _STRIPE_STATUS_MAP.get(stripe_status, SubscriptionStatus.CANCELED)


@router.get("/usage", response_model=UsageSnapshot, dependencies=[Depends(get_current_membership)])
async def get_billing_usage(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> UsageSnapshot:
    _, membership = membership_pair
    return await get_usage(session, membership.workspace_id)


@router.post(
    "/checkout", response_model=CheckoutResult, dependencies=[Depends(get_current_membership)]
)
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CheckoutResult:
    user, membership = membership_pair
    settings = request.app.state.settings
    price_id = (
        settings.stripe_price_id_pro if payload.tier == "pro" else settings.stripe_price_id_team
    )

    subscription = await get_or_create_subscription(session, membership.workspace_id)
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


@router.post("/portal", response_model=PortalResult, dependencies=[Depends(get_current_membership)])
async def create_portal(
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
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
        updated_subscription.status = _map_stripe_status(data.get("status", ""))
        if updated_subscription.status == SubscriptionStatus.CANCELED:
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
