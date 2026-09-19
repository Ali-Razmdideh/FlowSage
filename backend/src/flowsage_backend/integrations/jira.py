"""Jira Cloud REST client: creates a single issue via `/rest/api/3/issue`. Auth
is HTTP Basic with an email + API token, per Jira Cloud's documented scheme."""

from __future__ import annotations

import httpx

from flowsage_backend.outbound_http import outbound_client
from flowsage_backend.url_safety import UnsafeUrlError, validate_outbound_url


class JiraNotConfiguredError(Exception):
    """Raised when base_url/email/api_token/project_key aren't all set."""


class JiraDeliveryError(Exception):
    """Raised when Jira rejects the issue-creation POST."""


async def create_jira_issue(
    *,
    base_url: str | None,
    email: str | None,
    api_token: str | None,
    project_key: str | None,
    summary: str,
    description: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    if not (base_url and email and api_token and project_key):
        raise JiraNotConfiguredError(
            "Jira is not fully configured (need JIRA_BASE_URL, JIRA_EMAIL, "
            "JIRA_API_TOKEN, JIRA_PROJECT_KEY)"
        )

    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                ],
            },
            "issuetype": {"name": "Bug"},
        }
    }

    try:
        validate_outbound_url(base_url)
        async with outbound_client(
            transport=transport, auth=httpx.BasicAuth(email, api_token)
        ) as client:
            response = await client.post(f"{base_url.rstrip('/')}/rest/api/3/issue", json=payload)
    except (httpx.HTTPError, UnsafeUrlError):
        raise JiraDeliveryError("Jira delivery failed") from None

    if response.status_code != 201:
        raise JiraDeliveryError(f"Jira issue creation returned {response.status_code}")

    key = response.json()["key"]
    assert isinstance(key, str)
    return key
