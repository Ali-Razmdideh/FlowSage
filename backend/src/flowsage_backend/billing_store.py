"""Per-workspace accessor for `WorkspaceSubscription`. Created lazily on first
access, defaulting to Free tier -- mirrors `settings_store.py`'s
`get_or_create_calibration_settings` exactly."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.billing import WorkspaceSubscription


async def _select_subscription(
    session: AsyncSession, workspace_id: uuid.UUID
) -> WorkspaceSubscription | None:
    result = await session.execute(
        select(WorkspaceSubscription)
        .where(WorkspaceSubscription.workspace_id == workspace_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_or_create_subscription(
    session: AsyncSession, workspace_id: uuid.UUID
) -> WorkspaceSubscription:
    subscription = await _select_subscription(session, workspace_id)
    if subscription is not None:
        return subscription

    subscription = WorkspaceSubscription(workspace_id=workspace_id)
    session.add(subscription)
    try:
        await session.commit()
    except IntegrityError:
        # A workspace's very first burst of concurrent requests (e.g. several
        # `POST /v1/events` calls landing at once, each running
        # `check_within_limits` -> `get_usage` -> here) can all miss the SELECT
        # above and all try to INSERT. The unique index on `workspace_id` lets
        # exactly one win; the losers must not surface a 500 on the ingestion
        # endpoint. Roll back and re-read -- the winner's row is committed and
        # visible by now.
        await session.rollback()
        subscription = await _select_subscription(session, workspace_id)
        if subscription is None:
            # Not the race we're guarding against: a genuine constraint
            # violation (e.g. the workspace row itself is gone -> FK failure).
            # Let it propagate.
            raise
        return subscription

    await session.refresh(subscription)
    return subscription
