# backend/src/flowsage_backend/integrations/stripe_client.py
"""Stripe SDK wrapper: hosted Checkout (upgrade), hosted Customer Portal
(manage/cancel), and webhook signature verification. Mirrors
`integrations/slack.py`'s shape -- thin functions, `StripeNotConfiguredError`
when the secret key is missing, no live network calls in this test suite.

The create calls use the SDK's `*_async` variants (`stripe==11.6.0` ships real
coroutine equivalents of every resource method). The synchronous
`Session.create(...)` does a blocking HTTP request, which inside these
`async def` wrappers would stall the whole event loop for the duration of the
Stripe round-trip."""

from __future__ import annotations

import uuid
from typing import cast

import stripe


class StripeNotConfiguredError(Exception):
    """Raised when STRIPE_SECRET_KEY is not configured."""


async def create_checkout_session(
    *,
    secret_key: str | None,
    price_id: str | None,
    customer_email: str,
    existing_customer_id: str | None,
    workspace_id: uuid.UUID,
    tier: str,
    success_url: str,
    cancel_url: str,
) -> str:
    if secret_key is None or price_id is None:
        raise StripeNotConfiguredError("Stripe is not configured for this tier")

    # Stripe's TypedDict-based `**params: Unpack[...]` signature can't accept a
    # plain `dict[str, str]` spread under mypy --strict (it doesn't widen to the
    # TypedDict), so the customer/customer_email branches are two literal calls
    # instead of one call with a merged kwargs dict.
    if existing_customer_id is not None:
        session = await stripe.checkout.Session.create_async(
            api_key=secret_key,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"workspace_id": str(workspace_id), "tier": tier},
            customer=existing_customer_id,
        )
    else:
        session = await stripe.checkout.Session.create_async(
            api_key=secret_key,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"workspace_id": str(workspace_id), "tier": tier},
            customer_email=customer_email,
        )
    assert session.url is not None
    return session.url


async def create_portal_session(
    *, secret_key: str | None, customer_id: str, return_url: str
) -> str:
    if secret_key is None:
        raise StripeNotConfiguredError("Stripe is not configured")

    session = await stripe.billing_portal.Session.create_async(
        api_key=secret_key, customer=customer_id, return_url=return_url
    )
    assert session.url is not None
    return session.url


def verify_webhook(*, payload: bytes, sig_header: str, webhook_secret: str) -> stripe.Event:
    # stripe.Webhook.construct_event is untyped in the installed SDK version
    # (no annotations, despite the package otherwise shipping inline types),
    # so mypy --strict needs both the call and the return value silenced/cast.
    event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
        payload, sig_header, webhook_secret
    )
    return cast(stripe.Event, event)
