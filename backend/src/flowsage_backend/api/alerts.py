"""Trend alert summary (for the dashboard banner) and a manually-triggerable
weekly digest send -- the same digest content `worker.py`'s arq cron job posts
on schedule, exposed here so it can be tested/fired without waiting a week."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.alerts import (
    AlertsReport,
    build_alerts_report,
    build_digest_blocks,
    build_digest_text,
)
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)
from flowsage_backend.integrations_store import get_slack_integration
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership

router = APIRouter(
    prefix="/alerts", tags=["alerts"], dependencies=[Depends(get_current_membership)]
)


class DigestResult(BaseModel):
    status: str = "sent"


@router.get("", response_model=AlertsReport)
async def get_alerts(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> AlertsReport:
    _, membership = membership_pair
    return await build_alerts_report(session, membership.workspace_id)


@router.post("/digest/run", response_model=DigestResult)
async def run_digest_now(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> DigestResult:
    _, membership = membership_pair
    integration = await get_slack_integration(session, membership.workspace_id)
    report = await build_alerts_report(session, membership.workspace_id)
    try:
        await post_slack_message(
            integration.webhook_url if integration else None,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return DigestResult()
