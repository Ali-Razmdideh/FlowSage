from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import (
    ApiKey,
    JiraIntegration,
    SlackIntegration,
    Webhook,
    WebhookDelivery,
)
from flowsage_backend.models.workspace import Workspace


async def _make_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Models Test", slug=f"models-test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


async def test_api_key_round_trips(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    key = ApiKey(
        workspace_id=workspace_id,
        name="CI key",
        key_prefix="fs_live_ab12",
        key_hash="a" * 64,
    )
    db_session.add(key)
    await db_session.commit()

    result = await db_session.execute(select(ApiKey).where(ApiKey.workspace_id == workspace_id))
    fetched = result.scalar_one()
    assert fetched.name == "CI key"
    assert fetched.revoked_at is None
    assert fetched.last_used_at is None


async def test_slack_and_jira_integration_round_trip(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    db_session.add(SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/x"))
    db_session.add(
        JiraIntegration(
            workspace_id=workspace_id,
            base_url="https://acme.atlassian.net",
            email="bot@acme.test",
            api_token="tok",
            project_key="FS",
        )
    )
    await db_session.commit()

    slack = (
        await db_session.execute(select(SlackIntegration).where(SlackIntegration.workspace_id == workspace_id))
    ).scalar_one()
    jira = (
        await db_session.execute(select(JiraIntegration).where(JiraIntegration.workspace_id == workspace_id))
    ).scalar_one()
    assert slack.webhook_url == "https://hooks.slack.test/x"
    assert jira.project_key == "FS"


async def test_webhook_delivery_cascades_on_webhook_delete(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    webhook = Webhook(
        workspace_id=workspace_id,
        url="https://example.test/hook",
        secret="s3cr3t",
        event_types=["alert.triggered"],
    )
    db_session.add(webhook)
    await db_session.commit()
    await db_session.refresh(webhook)

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event_type="alert.triggered",
        payload="{}",
        status_code=200,
        success=True,
    )
    db_session.add(delivery)
    await db_session.commit()
    await db_session.refresh(delivery)
    delivery_id = delivery.id

    await db_session.delete(webhook)
    await db_session.commit()

    remaining = await db_session.execute(
        select(WebhookDelivery).where(WebhookDelivery.id == delivery_id)
    )
    assert remaining.scalar_one_or_none() is None
