"""Pilot onboarding endpoints: `GET /onboarding/status` (checklist) and
`POST /onboarding/import-sample-data` (Task 5)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership
from flowsage_backend.onboarding import (
    ImportSampleDataResult,
    OnboardingStatus,
    get_onboarding_status,
    import_sample_data,
)
from flowsage_backend.simulations import SimulationError

router = APIRouter(
    prefix="/onboarding", tags=["onboarding"], dependencies=[Depends(get_current_membership)]
)


@router.get("/status", response_model=OnboardingStatus)
async def onboarding_status(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> OnboardingStatus:
    _, membership = membership_pair
    return await get_onboarding_status(session, membership.workspace_id)


@router.post(
    "/import-sample-data",
    response_model=ImportSampleDataResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_sample_data_endpoint(
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> ImportSampleDataResult:
    _, membership = membership_pair
    settings = request.app.state.settings
    run_id = uuid.uuid4()
    screenshots_dir = Path(settings.upload_dir) / str(run_id)

    try:
        result = await import_sample_data(
            session,
            workspace_id=membership.workspace_id,
            run_id=run_id,
            screenshots_dest_dir=screenshots_dir,
        )
    except SimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await request.app.state.arq_pool.enqueue_job("run_simulation_job", str(result.run_id))
    return result
