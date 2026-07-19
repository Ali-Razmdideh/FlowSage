"""Slack webhook client. A webhook is a single POST -- no SDK needed.

The `transport` parameter exists purely for tests (`httpx.MockTransport`),
mirroring the `ASGITransport` idiom this codebase's own API tests already use.
"""

from __future__ import annotations

import httpx


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

    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(webhook_url, json=payload)

    if response.status_code != 200:
        msg = f"Slack webhook returned {response.status_code}: {response.text}"
        raise SlackDeliveryError(msg)
