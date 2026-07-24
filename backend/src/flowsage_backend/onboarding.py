"""Pilot onboarding tooling: a compute-on-demand checklist (`GET /onboarding/status`,
no new table -- same pattern as `calibration.py`/`churn.py`) and a one-click sample
data importer (`POST /onboarding/import-sample-data`) that reuses the exact same
`ingest_events()` and simulation pipeline (`create_run()` + `run_simulation_job`) a
real user's upload goes through -- see the Phase 3 chunk 4 design spec.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership


class OnboardingStatus(BaseModel):
    has_api_key: bool
    has_events: bool
    has_completed_simulation: bool
    has_multiple_members: bool


async def get_onboarding_status(session: AsyncSession, workspace_id: uuid.UUID) -> OnboardingStatus:
    has_api_key = (
        await session.execute(
            select(ApiKey.id)
            .where(ApiKey.workspace_id == workspace_id, ApiKey.revoked_at.is_(None))
            .limit(1)
        )
    ).first() is not None

    has_events = (
        await session.execute(select(Event.id).where(Event.workspace_id == workspace_id).limit(1))
    ).first() is not None

    has_completed_simulation = (
        await session.execute(
            select(SimulationRun.id)
            .where(
                SimulationRun.workspace_id == workspace_id,
                SimulationRun.status == RunStatus.COMPLETED,
            )
            .limit(1)
        )
    ).first() is not None

    member_count = (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)
        )
    ).scalar_one()

    return OnboardingStatus(
        has_api_key=has_api_key,
        has_events=has_events,
        has_completed_simulation=has_completed_simulation,
        has_multiple_members=member_count >= 2,
    )
