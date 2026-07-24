"""Pilot onboarding endpoints: `GET /onboarding/status` (checklist) and
`POST /onboarding/import-sample-data` (Task 5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership
from flowsage_backend.onboarding import OnboardingStatus, get_onboarding_status

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
