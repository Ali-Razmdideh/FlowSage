"""CRUD + delivery-log helpers for `Webhook`/`WebhookDelivery`."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.webhook import Webhook, WebhookDelivery


async def record_delivery(
    session: AsyncSession,
    webhook_id: uuid.UUID,
    event_type: str,
    payload: dict[str, object],
    status_code: int | None,
    success: bool,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        webhook_id=webhook_id,
        event_type=event_type,
        payload=json.dumps(payload),
        status_code=status_code,
        success=success,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def list_deliveries(
    session: AsyncSession, webhook_id: uuid.UUID, limit: int = 50
) -> list[WebhookDelivery]:
    result = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_enabled_webhooks_for_event(
    session: AsyncSession, workspace_id: uuid.UUID, event_type: str
) -> list[Webhook]:
    result = await session.execute(
        select(Webhook).where(
            Webhook.workspace_id == workspace_id,
            Webhook.enabled.is_(True),
        )
    )
    return [w for w in result.scalars().all() if event_type in w.event_types]
