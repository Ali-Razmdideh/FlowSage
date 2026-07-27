import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing import TIER_LIMITS, get_usage
from flowsage_backend.models.billing import SubscriptionTier, WorkspaceSubscription
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.seed import seed_baseline_personas, upsert_user


def _month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def test_get_usage_counts_events_runs_and_seats_this_month(
    db_session: AsyncSession,
) -> None:
    workspace = Workspace(name="Usage Test", slug=f"usage-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()

    this_month = _month_start_utc() + timedelta(days=1)
    last_month = _month_start_utc() - timedelta(days=1)

    db_session.add_all(
        [
            Event(
                workspace_id=workspace.id,
                session_id="s1",
                screen="landing",
                event="screen_view",
                timestamp=this_month,
            ),
            Event(
                workspace_id=workspace.id,
                session_id="s1",
                screen="cart",
                event="screen_view",
                timestamp=this_month,
            ),
            Event(
                workspace_id=workspace.id,
                session_id="s2",
                screen="landing",
                event="screen_view",
                timestamp=last_month,
            ),
        ]
    )

    persona_user = await upsert_user(
        db_session, f"usage-persona-{uuid.uuid4().hex[:8]}@example.com", "hunter2"
    )
    persona_membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == persona_user.id))
    ).scalar_one()
    personas = await seed_baseline_personas(db_session, persona_membership.workspace_id)
    persona = personas[0]

    db_session.add_all(
        [
            SimulationRun(
                workspace_id=workspace.id,
                flow_name="checkout",
                goal="buy",
                persona_id=persona.id,
                screenshots_dir="/tmp/does-not-matter",
                status=RunStatus.COMPLETED,
            ),
            SimulationRun(
                workspace_id=workspace.id,
                flow_name="checkout",
                goal="buy",
                persona_id=persona.id,
                screenshots_dir="/tmp/does-not-matter",
                status=RunStatus.COMPLETED,
                created_at=last_month,
            ),
        ]
    )

    admin_user = await upsert_user(
        db_session, f"usage-admin-{uuid.uuid4().hex[:8]}@example.com", "hunter2"
    )
    db_session.add(Membership(user_id=admin_user.id, workspace_id=workspace.id, role=Role.ADMIN))
    await db_session.commit()

    usage = await get_usage(db_session, workspace.id)

    assert usage.tier == SubscriptionTier.FREE
    assert usage.events_used == 2  # only this-month events counted
    assert usage.events_limit == TIER_LIMITS[SubscriptionTier.FREE].events_per_month
    assert usage.runs_used == 1  # only this-month run counted
    assert usage.runs_limit == TIER_LIMITS[SubscriptionTier.FREE].runs_per_month
    assert usage.seats_used == 1
    assert usage.seats_limit == TIER_LIMITS[SubscriptionTier.FREE].seats


async def test_get_usage_reflects_upgraded_tier(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Usage Pro Test", slug=f"usage-pro-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(WorkspaceSubscription(workspace_id=workspace.id, tier=SubscriptionTier.PRO))
    await db_session.commit()

    usage = await get_usage(db_session, workspace.id)

    assert usage.tier == SubscriptionTier.PRO
    assert usage.events_limit == TIER_LIMITS[SubscriptionTier.PRO].events_per_month
