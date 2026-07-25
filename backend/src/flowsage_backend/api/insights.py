"""Public, API-key-authenticated read endpoints for external integrations
(`/v1/insights/...`). Reuses the same `require_workspace_api_key` dependency
`POST /v1/events` already uses -- no new auth mechanism. The `APIKeyHeader`
security scheme below is purely additive documentation: it makes Swagger UI
show an Authorize control for this router, but `require_workspace_api_key`
still independently reads and validates the header itself."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import APIKeyHeader
from flowsage_graph.models import FunnelReport
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_db_session, require_workspace_api_key
from flowsage_backend.events import build_funnel_report
from flowsage_backend.insights import list_friction_issues

_api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

insights_router = APIRouter(
    prefix="/v1/insights",
    tags=["insights"],
    dependencies=[Security(_api_key_header_scheme)],
)


class FrictionIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    screen: str
    severity: str
    title: str
    heuristic_violated: str
    persona_impact: str
    description: str
    suggested_fix: str
    created_at: datetime


class FrictionIssuePageOut(BaseModel):
    issues: list[FrictionIssueOut]
    next_cursor: str | None


@insights_router.get("/funnel", response_model=FunnelReport)
async def insights_funnel(
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    workspace_id: uuid.UUID = Depends(require_workspace_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> FunnelReport:
    return await build_funnel_report(
        session, workspace_id, cohort=cohort, device=device, since=since
    )


@insights_router.get("/friction-issues", response_model=FrictionIssuePageOut)
async def insights_friction_issues(
    severity: str | None = Query(default=None),
    screen: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    workspace_id: uuid.UUID = Depends(require_workspace_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> FrictionIssuePageOut:
    issues, next_cursor = await list_friction_issues(
        session,
        workspace_id,
        severity=severity,
        screen=screen,
        since=since,
        cursor=cursor,
        limit=limit,
    )
    return FrictionIssuePageOut(
        issues=[FrictionIssueOut.model_validate(i) for i in issues], next_cursor=next_cursor
    )
