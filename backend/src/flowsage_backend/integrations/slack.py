"""Slack webhook client. A webhook is a single POST -- no SDK needed.

The `transport` parameter exists purely for tests (`httpx.MockTransport`),
mirroring the `ASGITransport` idiom this codebase's own API tests already use.
"""

from __future__ import annotations

import httpx

from flowsage_backend.outbound_http import outbound_client
from flowsage_backend.url_safety import UnsafeUrlError, validate_outbound_url


class SlackNotConfiguredError(Exception):
    """Raised when no Slack webhook URL is configured."""


class SlackDeliveryError(Exception):
    """Raised when Slack rejects the webhook POST."""


async def post_slack_message(
    webhook_url: str | None,
    *,
    text: str,
    blocks: list[dict[str, object]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    if webhook_url is None:
        raise SlackNotConfiguredError("SLACK_WEBHOOK_URL is not configured")

    payload: dict[str, object] = {"text": text}
    if blocks is not None:
        payload["blocks"] = blocks

    try:
        validate_outbound_url(webhook_url)
        async with outbound_client(transport=transport) as client:
            response = await client.post(webhook_url, json=payload)
    except (httpx.HTTPError, UnsafeUrlError):
        raise SlackDeliveryError("Slack delivery failed") from None

    if response.status_code != 200:
        msg = f"Slack webhook returned {response.status_code}"
        raise SlackDeliveryError(msg)
