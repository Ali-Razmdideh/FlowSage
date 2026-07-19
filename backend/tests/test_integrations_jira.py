import httpx
import pytest

from flowsage_backend.integrations.jira import (
    JiraDeliveryError,
    JiraNotConfiguredError,
    create_jira_issue,
)


async def test_create_jira_issue_raises_when_not_configured() -> None:
    with pytest.raises(JiraNotConfiguredError):
        await create_jira_issue(
            base_url=None,
            email=None,
            api_token=None,
            project_key=None,
            summary="s",
            description="d",
        )


async def test_create_jira_issue_raises_when_partially_configured() -> None:
    with pytest.raises(JiraNotConfiguredError):
        await create_jira_issue(
            base_url="https://example.atlassian.net",
            email="bot@example.com",
            api_token=None,
            project_key="FLOW",
            summary="s",
            description="d",
        )


async def test_create_jira_issue_posts_and_returns_key() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(201, json={"key": "FLOW-42"})

    transport = httpx.MockTransport(handler)
    key = await create_jira_issue(
        base_url="https://example.atlassian.net",
        email="bot@example.com",
        api_token="token123",
        project_key="FLOW",
        summary="Checkout drop-off",
        description="42% drop-off at checkout.",
        transport=transport,
    )

    assert key == "FLOW-42"
    assert captured["url"] == "https://example.atlassian.net/rest/api/3/issue"
    assert b"Checkout drop-off" in captured["body"]  # type: ignore[operator]
    assert b"FLOW" in captured["body"]  # type: ignore[operator]


async def test_create_jira_issue_raises_on_non_201() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text="bad request"))
    with pytest.raises(JiraDeliveryError, match="400"):
        await create_jira_issue(
            base_url="https://example.atlassian.net",
            email="bot@example.com",
            api_token="token123",
            project_key="FLOW",
            summary="s",
            description="d",
            transport=transport,
        )
