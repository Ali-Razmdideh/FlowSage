import httpx
import pytest

from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)


async def test_post_slack_message_raises_when_not_configured() -> None:
    with pytest.raises(SlackNotConfiguredError):
        await post_slack_message(None, text="hello")


async def test_post_slack_message_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    await post_slack_message(
        "https://hooks.slack.com/services/x/y/z",
        text="fallback text",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}],
        transport=transport,
    )

    assert captured["url"] == "https://hooks.slack.com/services/x/y/z"
    assert b"fallback text" in captured["body"]  # type: ignore[operator]
    assert b"section" in captured["body"]  # type: ignore[operator]


async def test_post_slack_message_raises_on_non_200() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text="invalid_payload"))
    with pytest.raises(SlackDeliveryError, match="400"):
        await post_slack_message(
            "https://hooks.slack.com/services/x/y/z", text="hi", transport=transport
        )
