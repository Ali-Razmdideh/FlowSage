from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog, Event
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.worker import _purge_workspace_retention
from tests.conftest import create_workspace_and_admin


async def test_purge_deletes_audit_logs_and_events_older_than_retention(
    db_session: AsyncSession,
) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"purge-{uuid.uuid4().hex[:8]}@example.com"
    )

    workspace = await db_session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    workspace.retention_days = 30
    await db_session.commit()

    now = datetime.now(timezone.utc)
    old_log = AuditLog(
        workspace_id=membership.workspace_id, action="old.event", created_at=now - timedelta(days=31)
    )
    recent_log = AuditLog(
        workspace_id=membership.workspace_id, action="recent.event", created_at=now - timedelta(days=1)
    )
    db_session.add_all([old_log, recent_log])
    await db_session.commit()

    old_event = Event(
        workspace_id=membership.workspace_id,
        session_id="purge-s1",
        event="page_view",
        screen="landing",
        timestamp=now - timedelta(days=31),
        device="desktop",
        cohort="paid_users",
    )
    recent_event = Event(
        workspace_id=membership.workspace_id,
        session_id="purge-s2",
        event="page_view",
        screen="landing",
        timestamp=now - timedelta(days=1),
        device="desktop",
        cohort="paid_users",
    )
    db_session.add_all([old_event, recent_event])
    await db_session.commit()

    await _purge_workspace_retention(db_session, membership.workspace_id, workspace.retention_days)

    remaining_logs = (
        (await db_session.execute(select(AuditLog).where(AuditLog.workspace_id == membership.workspace_id)))
        .scalars()
        .all()
    )
    assert {log.action for log in remaining_logs} == {"recent.event"}

    remaining_events = (
        (await db_session.execute(select(Event).where(Event.workspace_id == membership.workspace_id)))
        .scalars()
        .all()
    )
    assert {e.session_id for e in remaining_events} == {"purge-s2"}
