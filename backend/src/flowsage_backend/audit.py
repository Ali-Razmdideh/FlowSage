"""Audit log write/query helpers. `record_audit_event` is called inline from route
handlers right after the action it's logging succeeds; it never raises -- a failed
audit write must not roll back or fail the action it's documenting (mirrors the
existing Neo4j-mirror-write best-effort pattern in events.py)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


async def record_audit_event(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID | None = None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    extra_data: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    try:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                extra_data=extra_data or {},
                ip_address=ip_address,
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - a broken audit write must never block the
        # action it's documenting (e.g. a bad workspace_id, a DB hiccup).
        await session.rollback()
        logger.warning(
            "Failed to record audit event %r for workspace %s", action, workspace_id, exc_info=True
        )


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at_str, id_str = cursor.split("|", 1)
    return datetime.fromisoformat(created_at_str), uuid.UUID(id_str)


def _encode_cursor(entry: AuditLog) -> str:
    return f"{entry.created_at.isoformat()}|{entry.id}"


async def list_audit_logs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    action: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AuditLog], str | None]:
    query = select(AuditLog).where(AuditLog.workspace_id == workspace_id)
    if action is not None:
        query = query.where(AuditLog.action == action)
    if actor_user_id is not None:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                AuditLog.created_at < cursor_created_at,
                and_(AuditLog.created_at == cursor_created_at, AuditLog.id < cursor_id),
            )
        )
    query = query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)

    result = await session.execute(query)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more else None
    return page, next_cursor
