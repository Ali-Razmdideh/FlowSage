"""Per-workspace accessor for `WorkspaceSubscription`. Created lazily on first
access, defaulting to Free tier -- mirrors `settings_store.py`'s
`get_or_create_calibration_settings` exactly."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.billing import WorkspaceSubscription


async def get_or_create_subscription(
    session: AsyncSession, workspace_id: uuid.UUID
) -> WorkspaceSubscription:
    result = await session.execute(
        select(WorkspaceSubscription)
        .where(WorkspaceSubscription.workspace_id == workspace_id)
        .limit(1)
    )
    subscription = result.scalar_one_or_none()
    if subscription is not None:
        return subscription

    subscription = WorkspaceSubscription(workspace_id=workspace_id)
    session.add(subscription)
    await session.commit()
    await session.refresh(subscription)
    return subscription
