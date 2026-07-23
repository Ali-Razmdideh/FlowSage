"""Per-workspace Slack/Jira config lookup, replacing the global `Settings.slack_webhook_url`/
`jira_*` env vars. Mirrors `settings_store.py`'s shape, but returns `None` rather than
lazily creating a row -- "not configured" is a real, common state here (most workspaces
won't connect Slack/Jira), unlike `CalibrationSettings` which every workspace has."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.integration import JiraIntegration, SlackIntegration


async def get_slack_integration(
    session: AsyncSession, workspace_id: uuid.UUID
) -> SlackIntegration | None:
    result = await session.execute(
        select(SlackIntegration).where(SlackIntegration.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def get_jira_integration(
    session: AsyncSession, workspace_id: uuid.UUID
) -> JiraIntegration | None:
    result = await session.execute(
        select(JiraIntegration).where(JiraIntegration.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()
