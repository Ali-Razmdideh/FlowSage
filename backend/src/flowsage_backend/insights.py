"""Compute-on-demand queries backing the public `/v1/insights/*` API
(`api/insights.py`). No new tables -- mirrors `calibration.py`/`churn.py`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.simulation import FrictionIssue


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at_str, id_str = cursor.split("|", 1)
    return datetime.fromisoformat(created_at_str), uuid.UUID(id_str)


def _encode_cursor(issue: FrictionIssue) -> str:
    return f"{issue.created_at.isoformat()}|{issue.id}"


async def list_friction_issues(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    severity: str | None = None,
    screen: str | None = None,
    since: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[FrictionIssue], str | None]:
    query = select(FrictionIssue).where(FrictionIssue.workspace_id == workspace_id)
    if severity is not None:
        query = query.where(FrictionIssue.severity == severity)
    if screen is not None:
        query = query.where(FrictionIssue.screen == screen)
    if since is not None:
        query = query.where(FrictionIssue.created_at >= since)
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                FrictionIssue.created_at < cursor_created_at,
                and_(
                    FrictionIssue.created_at == cursor_created_at,
                    FrictionIssue.id < cursor_id,
                ),
            )
        )
    query = query.order_by(FrictionIssue.created_at.desc(), FrictionIssue.id.desc()).limit(
        limit + 1
    )

    result = await session.execute(query)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more else None
    return page, next_cursor
