# backend/tests/test_integrations_stripe.py
import hashlib
import hmac
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
import stripe

from flowsage_backend.integrations.stripe_client import (
    StripeNotConfiguredError,
    create_checkout_session,
    create_portal_session,
    verify_webhook,
)


async def test_create_checkout_session_requires_secret_key() -> None:
    with pytest.raises(StripeNotConfiguredError):
        await create_checkout_session(
            secret_key=None,
            price_id="price_123",
            customer_email="a@example.com",
            existing_customer_id=None,
            workspace_id=uuid.uuid4(),
            tier="pro",
            success_url="https://app.example.com/settings/billing?checkout=success",
            cancel_url="https://app.example.com/settings/billing?checkout=cancel",
        )


async def test_create_checkout_session_returns_url() -> None:
    fake_session = MagicMock(url="https://checkout.stripe.com/pay/cs_test_123")
    with patch("stripe.checkout.Session.create", return_value=fake_session) as mock_create:
        url = await create_checkout_session(
            secret_key="sk_test_fake",
            price_id="price_123",
            customer_email="a@example.com",
            existing_customer_id=None,
            workspace_id=uuid.uuid4(),
            tier="pro",
            success_url="https://app.example.com/settings/billing?checkout=success",
            cancel_url="https://app.example.com/settings/billing?checkout=cancel",
        )
    assert url == "https://checkout.stripe.com/pay/cs_test_123"
    assert mock_create.call_args.kwargs["mode"] == "subscription"
    assert mock_create.call_args.kwargs["customer_email"] == "a@example.com"


async def test_create_portal_session_requires_secret_key() -> None:
    with pytest.raises(StripeNotConfiguredError):
        await create_portal_session(
            secret_key=None,
            customer_id="cus_123",
            return_url="https://app.example.com/settings/billing",
        )


async def test_create_portal_session_returns_url() -> None:
    fake_session = MagicMock(url="https://billing.stripe.com/session/bps_test_123")
    with patch("stripe.billing_portal.Session.create", return_value=fake_session):
        url = await create_portal_session(
            secret_key="sk_test_fake",
            customer_id="cus_123",
            return_url="https://app.example.com/settings/billing",
        )
    assert url == "https://billing.stripe.com/session/bps_test_123"


def test_verify_webhook_valid_signature_roundtrip() -> None:
    payload = b'{"id": "evt_test", "type": "checkout.session.completed"}'
    secret = "whsec_test_fake"
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    sig_header = f"t={timestamp},v1={signature}"

    event = verify_webhook(payload=payload, sig_header=sig_header, webhook_secret=secret)
    assert event["type"] == "checkout.session.completed"


def test_verify_webhook_rejects_bad_signature() -> None:
    payload = b'{"id": "evt_test", "type": "checkout.session.completed"}'
    with pytest.raises(stripe.SignatureVerificationError):
        verify_webhook(
            payload=payload, sig_header="t=1,v1=deadbeef", webhook_secret="whsec_test_fake"
        )
