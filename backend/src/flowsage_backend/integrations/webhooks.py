"""Outbound delivery for custom webhook subscriptions (`/settings/integrations`).
Same "never raise, let the caller log a delivery row either way" contract as
`slack.py`'s `post_slack_message` almost has -- except here failure is an expected,
routine outcome (a user's endpoint being down shouldn't be an exception the digest
job has to catch), so this returns a `(status_code, success)` tuple instead of raising."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from flowsage_backend.outbound_http import outbound_client
from flowsage_backend.url_safety import UnsafeUrlError, validate_outbound_url


async def deliver_webhook(
    url: str,
    *,
    secret: str,
    event_type: str,
    payload: dict[str, object],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int | None, bool]:
    body = json.dumps({"event": event_type, "data": payload}).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    try:
        validate_outbound_url(url)
        async with outbound_client(transport=transport) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-FlowSage-Signature": signature,
                },
            )
    except (httpx.HTTPError, UnsafeUrlError):
        return None, False

    success = 200 <= response.status_code < 300
    return response.status_code, success
