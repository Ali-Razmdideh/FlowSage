"""Freemium tier limits and on-demand usage counting.

Everything here is computed on demand from current data (no counters/aggregate
table), same philosophy as `calibration.py`/`churn.py`. Tier limits are code
constants, not DB rows -- nothing here needs to be admin-editable yet."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import SimulationRun
from flowsage_backend.models.workspace import Membership


class TierLimits(BaseModel):
    events_per_month: int
    runs_per_month: int
    seats: int  # -1 means unlimited


TIER_LIMITS: dict[SubscriptionTier, TierLimits] = {
    SubscriptionTier.FREE: TierLimits(events_per_month=1_000, runs_per_month=5, seats=1),
    SubscriptionTier.PRO: TierLimits(events_per_month=50_000, runs_per_month=100, seats=10),
    SubscriptionTier.TEAM: TierLimits(events_per_month=500_000, runs_per_month=1_000, seats=-1),
}


class UsageSnapshot(BaseModel):
    tier: SubscriptionTier
    events_used: int
    events_limit: int
    runs_used: int
    runs_limit: int
    seats_used: int
    seats_limit: int


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_usage(session: AsyncSession, workspace_id: uuid.UUID) -> UsageSnapshot:
    subscription = await get_or_create_subscription(session, workspace_id)
    limits = TIER_LIMITS[subscription.tier]
    month_start = _month_start()

    events_used = (
        await session.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.workspace_id == workspace_id, Event.timestamp >= month_start)
        )
    ).scalar_one()
    runs_used = (
        await session.execute(
            select(func.count())
            .select_from(SimulationRun)
            .where(
                SimulationRun.workspace_id == workspace_id,
                SimulationRun.created_at >= month_start,
            )
        )
    ).scalar_one()
    seats_used = (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.workspace_id == workspace_id)
        )
    ).scalar_one()

    return UsageSnapshot(
        tier=subscription.tier,
        events_used=events_used,
        events_limit=limits.events_per_month,
        runs_used=runs_used,
        runs_limit=limits.runs_per_month,
        seats_used=seats_used,
        seats_limit=limits.seats,
    )
