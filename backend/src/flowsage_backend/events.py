"""Event ingestion and the funnel/friction query built on top of it.

Raw events are stored in Postgres (source of truth for `build_funnel_report`,
which reuses `flowsage_graph`'s tested pure functions unchanged) and best-effort
mirrored into Neo4j as a temporal graph -- see `models/event.py` for why both.
"""

from __future__ import annotations

from datetime import datetime

from flowsage_graph.funnel import detect_friction, discover_funnel
from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FunnelReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event


async def ingest_events(session: AsyncSession, events: list[GraphEvent]) -> list[Event]:
    rows = [
        Event(
            session_id=e.session_id,
            screen=e.screen,
            event=e.event,
            timestamp=e.timestamp,
            device=e.device,
            cohort=e.cohort,
        )
        for e in events
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def query_events(
    session: AsyncSession,
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
) -> list[GraphEvent]:
    query = select(Event)
    if cohort is not None:
        query = query.where(Event.cohort == cohort)
    if device is not None:
        query = query.where(Event.device == device)
    if since is not None:
        query = query.where(Event.timestamp >= since)

    result = await session.execute(query.order_by(Event.timestamp))
    return [row.to_graph_event() for row in result.scalars().all()]


async def distinct_cohorts(
    session: AsyncSession,
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> list[str]:
    query = select(Event.cohort).distinct()
    if device is not None:
        query = query.where(Event.device == device)
    if since is not None:
        query = query.where(Event.timestamp >= since)

    result = await session.execute(query)
    return sorted(result.scalars().all())


async def build_funnel_report(
    session: AsyncSession,
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
) -> FunnelReport:
    events = await query_events(session, cohort=cohort, device=device, since=since)
    funnel = discover_funnel(events)
    friction = detect_friction(events, funnel)
    return FunnelReport(
        funnel=funnel,
        friction_nodes=friction,
        total_sessions=len({e.session_id for e in events}),
        total_events=len(events),
    )
