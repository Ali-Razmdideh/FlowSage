"""Event ingestion and the funnel/friction query built on top of it.

Raw events are stored in Postgres (source of truth for `build_funnel_report`,
which reuses `flowsage_graph`'s tested pure functions unchanged) and best-effort
mirrored into Neo4j as a temporal graph -- see `models/event.py` for why both.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from flowsage_graph.funnel import detect_friction, discover_funnel
from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FunnelReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event


async def ingest_events(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    events: list[GraphEvent],
    *,
    flow_ids: list[uuid.UUID | None] | None = None,
    flow_versions: list[int | None] | None = None,
) -> list[Event]:
    rows = [
        Event(
            workspace_id=workspace_id,
            session_id=e.session_id,
            screen=e.screen,
            event=e.event,
            timestamp=e.timestamp,
            device=e.device,
            cohort=e.cohort,
            flow_id=flow_ids[index] if flow_ids is not None else None,
            flow_version=flow_versions[index] if flow_versions is not None else None,
        )
        for index, e in enumerate(events)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def query_events(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
    flow_id: uuid.UUID | None = None,
    flow_version: int | None = None,
) -> list[GraphEvent]:
    query = select(Event).where(Event.workspace_id == workspace_id)
    if cohort is not None:
        query = query.where(Event.cohort == cohort)
    if device is not None:
        query = query.where(Event.device == device)
    if since is not None:
        query = query.where(Event.timestamp >= since)
    if flow_id is not None:
        query = query.where(Event.flow_id == flow_id)
    if flow_version is not None:
        query = query.where(Event.flow_version == flow_version)

    result = await session.execute(query.order_by(Event.timestamp))
    return [row.to_graph_event() for row in result.scalars().all()]


async def distinct_cohorts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> list[str]:
    query = select(Event.cohort).distinct().where(Event.workspace_id == workspace_id)
    if device is not None:
        query = query.where(Event.device == device)
    if since is not None:
        query = query.where(Event.timestamp >= since)

    result = await session.execute(query)
    return sorted(result.scalars().all())


async def build_funnel_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
    flow_id: uuid.UUID | None = None,
    flow_version: int | None = None,
) -> FunnelReport:
    events = await query_events(
        session, workspace_id, cohort=cohort, device=device, since=since,
        flow_id=flow_id, flow_version=flow_version,
    )
    funnel = discover_funnel(events)
    friction = detect_friction(events, funnel)
    return FunnelReport(
        funnel=funnel,
        friction_nodes=friction,
        total_sessions=len({e.session_id for e in events}),
        total_events=len(events),
    )
