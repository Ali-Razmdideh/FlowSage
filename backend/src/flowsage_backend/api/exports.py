"""Export actions for predicted `FrictionIssue` rows: the "Export to
Engineering Ticket"/"Export to Jira" buttons on the Predictive Engine's
friction report. Kept separate from `api/events.py`'s node-export endpoints
(Task 7) because these operate on a `SimulationRun`'s `FrictionIssue` id -- a
different lookup and domain object than an observational graph screen."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.integrations.jira import (
    JiraDeliveryError,
    JiraNotConfiguredError,
    create_jira_issue,
)
from flowsage_backend.integrations.slack import (
    SlackDeliveryError,
    SlackNotConfiguredError,
    post_slack_message,
)
from flowsage_backend.models.simulation import FrictionIssue
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership

router = APIRouter(
    prefix="/friction-issues", tags=["exports"], dependencies=[Depends(get_current_membership)]
)


class SlackExportResult(BaseModel):
    status: str = "sent"


class JiraExportResult(BaseModel):
    issue_key: str


async def _get_issue(
    session: AsyncSession, workspace_id: uuid.UUID, issue_id: uuid.UUID
) -> FrictionIssue:
    issue = await session.get(FrictionIssue, issue_id)
    if issue is None or issue.workspace_id != workspace_id:
        raise HTTPException(404, "Friction issue not found")
    return issue


@router.post("/{issue_id}/export/slack", response_model=SlackExportResult)
async def export_issue_to_slack(
    issue_id: uuid.UUID,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> SlackExportResult:
    _, membership = membership_pair
    issue = await _get_issue(session, membership.workspace_id, issue_id)
    settings = request.app.state.settings
    text = f"*{issue.severity.upper()}* friction on `{issue.screen}`: {issue.title}"
    try:
        await post_slack_message(settings.slack_webhook_url, text=text)
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return SlackExportResult()


@router.post("/{issue_id}/export/jira", response_model=JiraExportResult)
async def export_issue_to_jira(
    issue_id: uuid.UUID,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> JiraExportResult:
    _, membership = membership_pair
    issue = await _get_issue(session, membership.workspace_id, issue_id)
    settings = request.app.state.settings
    try:
        issue_key = await create_jira_issue(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            project_key=settings.jira_project_key,
            summary=f"[FlowSage] {issue.title}",
            description=f"{issue.description}\n\nSuggested fix: {issue.suggested_fix}",
        )
    except JiraNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JiraDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return JiraExportResult(issue_key=issue_key)
