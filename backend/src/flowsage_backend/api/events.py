"""Event ingestion (`POST /v1/events`, API-key auth) and the funnel/friction
query built on top of it (`GET /graph/funnel`, browser session auth)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FunnelReport
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_user, get_db_session, require_api_key
from flowsage_backend.events import build_funnel_report, ingest_events

logger = logging.getLogger(__name__)

events_router = APIRouter(
    prefix="/v1/events", tags=["events"], dependencies=[Depends(require_api_key)]
)
graph_router = APIRouter(prefix="/graph", tags=["graph"], dependencies=[Depends(get_current_user)])


class EventIn(BaseModel):
    session_id: str
    screen: str
    event: str
    timestamp: datetime
    device: str = "unknown"
    cohort: str = "unknown"


class IngestResult(BaseModel):
    ingested: int


@events_router.post("", response_model=IngestResult, status_code=201)
async def ingest(
    payload: list[EventIn],
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> IngestResult:
    graph_events = [GraphEvent.model_validate(e.model_dump()) for e in payload]
    rows = await ingest_events(session, graph_events)

    graph_sink = request.app.state.graph_sink
    try:
        await asyncio.to_thread(graph_sink.ingest, graph_events)
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
    session: AsyncSession = Depends(get_db_session),
) -> FunnelReport:
    return await build_funnel_report(session, cohort=cohort, device=device, since=since)
