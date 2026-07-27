import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing import TIER_LIMITS
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.event import Event
from flowsage_backend.models.workspace import Workspace

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
