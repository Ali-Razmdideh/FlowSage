"""Freemium tier limits and on-demand usage counting.

Everything here is computed on demand from current data (no counters/aggregate
table), same philosophy as `calibration.py`/`churn.py`. Tier limits are code
constants, not DB rows -- nothing here needs to be admin-editable yet."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.event import Event
from flowsage_backend.models.generated_insight import GeneratedInsight
from flowsage_backend.models.simulation import SimulationRun
from flowsage_backend.models.workspace import Membership


class TierLimits(BaseModel):
    events_per_month: int
    runs_per_month: int
    seats: int  # -1 means unlimited
    insight_generations_per_month: int  # -1 means unlimited


TIER_LIMITS: dict[SubscriptionTier, TierLimits] = {
    SubscriptionTier.FREE: TierLimits(
        events_per_month=1_000, runs_per_month=5, seats=1, insight_generations_per_month=20
    ),
    SubscriptionTier.PRO: TierLimits(
        events_per_month=50_000, runs_per_month=100, seats=10, insight_generations_per_month=1_000
    ),
    SubscriptionTier.TEAM: TierLimits(
        events_per_month=500_000, runs_per_month=1_000, seats=-1, insight_generations_per_month=-1
    ),
}


class UsageSnapshot(BaseModel):
    tier: SubscriptionTier
    events_used: int
    events_limit: int
    runs_used: int
    runs_limit: int
    seats_used: int
    seats_limit: int
    insight_generations_used: int
    insight_generations_limit: int


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
    insight_generations_used = (
        await session.execute(
            select(func.count())
            .select_from(GeneratedInsight)
            .where(
                GeneratedInsight.workspace_id == workspace_id,
                GeneratedInsight.created_at >= month_start,
            )
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
        insight_generations_used=insight_generations_used,
        insight_generations_limit=limits.insight_generations_per_month,
    )


async def check_within_limits(
    session: AsyncSession, workspace_id: uuid.UUID, resource: Literal["events", "runs", "seats"]
) -> None:
    """Raises 402 if the workspace is already at/over its tier's cap for
    `resource`. Checked BEFORE the action that would add one more unit (an
    event batch, a simulation run, a member) -- for event batches this means
    the check is a point-in-time gate, not a precise per-event cutoff: a
    workspace at 999/1000 posting a 50-event batch still gets the whole batch
    through. That's an intentional simplification, not a bug -- precise
    mid-batch cutoff isn't worth the complexity for a hard-cap freemium gate.
    """
    usage = await get_usage(session, workspace_id)
    used, limit = {
        "events": (usage.events_used, usage.events_limit),
        "runs": (usage.runs_used, usage.runs_limit),
        "seats": (usage.seats_used, usage.seats_limit),
    }[resource]

    if limit == -1:
        return
    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"{usage.tier.value.title()} plan limit reached for {resource} "
                f"({used}/{limit}). Upgrade to continue."
            ),
        )


async def has_narrative_budget(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Soft gate for narrative generation (Node Intelligence AI Insight,
    calibration anomaly narrative, retraining rationale) -- unlike
    `check_within_limits`, this never raises. A report page must never break
    because a text-generation budget ran out; callers fall back to the
    existing deterministic template text instead."""
    usage = await get_usage(session, workspace_id)
    if usage.insight_generations_limit == -1:
        return True
    return usage.insight_generations_used < usage.insight_generations_limit
