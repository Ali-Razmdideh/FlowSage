import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing import TIER_LIMITS
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import SimulationRun
from flowsage_backend.models.workspace import Membership, Workspace
from flowsage_backend.seed import seed_baseline_personas, upsert_user

from .conftest import create_api_key_for


async def _free_tier_workspace(db_session: AsyncSession, name: str) -> uuid.UUID:
    """A brand-new workspace, deliberately left at the default Free tier (no
    `WorkspaceSubscription` row) -- unlike `create_workspace_and_admin`/
    `ensure_default_workspace`, which Task 6 pinned to Team tier for
    unrelated tests' sake."""
    workspace = Workspace(name=name, slug=f"{name}-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return workspace.id


async def _free_tier_user_workspace(db_session: AsyncSession, prefix: str) -> tuple[str, uuid.UUID]:
    """Same idea as `_free_tier_workspace`, but for the cookie-authenticated
    routes (`POST /simulations`, `POST /workspaces/current/members`) rather
    than the API-key one: `upsert_user` mints a brand-new personal workspace
    with the caller as its sole admin and no `WorkspaceSubscription` row, so it
    reads as Free tier with exactly 1 seat used. `/auth/login` lands on that
    workspace (it picks the user's oldest membership)."""
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return email, membership.workspace_id


async def test_ingest_returns_402_when_over_free_event_cap(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _free_tier_workspace(db_session, "events-cap")
    api_key = await create_api_key_for(db_session, workspace_id)
    now = datetime.now(timezone.utc)
    cap = TIER_LIMITS[SubscriptionTier.FREE].events_per_month
    db_session.add_all(
        [
            Event(
                workspace_id=workspace_id,
                session_id=f"s{i}",
                screen="landing",
                event="screen_view",
                timestamp=now,
            )
            for i in range(cap)
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events",
            json=[
                {
                    "session_id": "over-cap",
                    "screen": "landing",
                    "event": "screen_view",
                    "timestamp": (now + timedelta(minutes=1)).isoformat(),
                }
            ],
            headers={"X-API-Key": api_key},
        )

    assert response.status_code == 402


async def test_create_simulation_returns_402_when_over_free_run_cap(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email, workspace_id = await _free_tier_user_workspace(db_session, "runs-cap")
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = next(p for p in personas if p.slug == "novice")

    cap = TIER_LIMITS[SubscriptionTier.FREE].runs_per_month
    db_session.add_all(
        [
            SimulationRun(
                workspace_id=workspace_id,
                flow_name="Checkout Flow",
                goal="Complete purchase",
                persona_id=persona.id,
                screenshots_dir="/tmp/does-not-matter",
            )
            for _ in range(cap)
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.post(
            "/simulations",
            data={
                "persona_id": str(persona.id),
                "goal": "Complete purchase",
                "flow_name": "Checkout Flow",
            },
            files={"files": ("shot.png", b"not-a-real-png", "image/png")},
        )

    assert response.status_code == 402


async def test_add_member_returns_402_when_over_free_seat_cap(
    app: FastAPI, db_session: AsyncSession
) -> None:
    # A fresh Free-tier workspace already sits at 1/1 seats (its admin), so the
    # very first invite must be refused -- even though the invitee exists.
    email, _ = await _free_tier_user_workspace(db_session, "seats-cap")
    invitee_email = f"seats-cap-invitee-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, invitee_email, "hunter2")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        response = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "viewer"}
        )

    assert response.status_code == 402
