"""Event ingestion (`POST /v1/events`, API-key auth) and the funnel/friction
query built on top of it (`GET /graph/funnel`, browser session auth)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FunnelReport
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.churn import (
    ChurnRiskSegment,
    CohortComparisonReport,
    NodeIntelligence,
    build_churn_risk_segments,
    compare_cohorts,
    get_node_intelligence,
)
from flowsage_backend.deps import get_current_membership, get_db_session, require_api_key
from flowsage_backend.events import build_funnel_report, ingest_events
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
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Workspace

logger = logging.getLogger(__name__)

events_router = APIRouter(
    prefix="/v1/events", tags=["events"], dependencies=[Depends(require_api_key)]
)
graph_router = APIRouter(
    prefix="/graph", tags=["graph"], dependencies=[Depends(get_current_membership)]
)


class EventIn(BaseModel):
    session_id: str
    screen: str
    event: str
    timestamp: datetime
    device: str = "unknown"
    cohort: str = "unknown"


class IngestResult(BaseModel):
    ingested: int


async def _default_workspace_id(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(select(Workspace.id).where(Workspace.slug == "fs-default"))
    workspace_id = result.scalar_one_or_none()
    if workspace_id is None:
        raise HTTPException(500, "No default workspace configured")
    return workspace_id


@events_router.post("", response_model=IngestResult, status_code=201)
async def ingest(
    payload: list[EventIn],
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> IngestResult:
    workspace_id = await _default_workspace_id(session)
    graph_events = [GraphEvent.model_validate(e.model_dump()) for e in payload]
    rows = await ingest_events(session, workspace_id, graph_events)

    graph_sink = request.app.state.graph_sink
    try:
        await asyncio.to_thread(graph_sink.ingest, graph_events, str(workspace_id))
    except Exception:  # noqa: BLE001 - Neo4j being unreachable shouldn't fail ingestion
        logger.warning(
            "Neo4j ingestion failed; events were still stored in Postgres", exc_info=True
        )

    return IngestResult(ingested=len(rows))


@graph_router.get("/funnel", response_model=FunnelReport)
async def funnel(
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> FunnelReport:
    _, membership = membership_pair
    return await build_funnel_report(
        session, membership.workspace_id, cohort=cohort, device=device, since=since
    )


@graph_router.get("/cohorts/compare", response_model=CohortComparisonReport)
async def cohorts_compare(
    cohorts: list[str] = Query(default=[]),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CohortComparisonReport:
    _, membership = membership_pair
    return await compare_cohorts(
        session, membership.workspace_id, cohorts, device=device, since=since
    )


@graph_router.get("/churn-risk", response_model=list[ChurnRiskSegment])
async def churn_risk(
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[ChurnRiskSegment]:
    _, membership = membership_pair
    return await build_churn_risk_segments(
        session, membership.workspace_id, device=device, since=since
    )


@graph_router.get("/nodes/{screen}", response_model=NodeIntelligence)
async def node_intelligence(
    screen: str,
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> NodeIntelligence:
    _, membership = membership_pair
    result = await get_node_intelligence(
        session, membership.workspace_id, screen, cohort=cohort, device=device, since=since
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"No funnel data for screen '{screen}'")
    return result


class SlackExportResult(BaseModel):
    status: str = "sent"


class JiraExportResult(BaseModel):
    issue_key: str


@graph_router.post("/nodes/{screen}/export/slack", response_model=SlackExportResult)
async def export_node_to_slack(
    screen: str,
    request: Request,
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> SlackExportResult:
    _, membership = membership_pair
    intel = await get_node_intelligence(
        session, membership.workspace_id, screen, cohort=cohort, device=device, since=since
    )
    if intel is None:
        raise HTTPException(status_code=404, detail=f"No funnel data for screen '{screen}'")

    settings = request.app.state.settings
    text = f"Friction node `{screen}`: {intel.ai_insight}"
    try:
        await post_slack_message(settings.slack_webhook_url, text=text)
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return SlackExportResult()


@graph_router.post("/nodes/{screen}/export/jira", response_model=JiraExportResult)
async def export_node_to_jira(
    screen: str,
    request: Request,
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> JiraExportResult:
    _, membership = membership_pair
    intel = await get_node_intelligence(
        session, membership.workspace_id, screen, cohort=cohort, device=device, since=since
    )
    if intel is None:
        raise HTTPException(status_code=404, detail=f"No funnel data for screen '{screen}'")

    settings = request.app.state.settings
    try:
        issue_key = await create_jira_issue(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=settings.jira_api_token,
            project_key=settings.jira_project_key,
            summary=f"[FlowSage] Friction node: {screen}",
            description=intel.ai_insight,
        )
    except JiraNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JiraDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return JiraExportResult(issue_key=issue_key)
