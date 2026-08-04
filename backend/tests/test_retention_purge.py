from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog, Event
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.seed import seed_baseline_personas
from flowsage_backend.worker import _purge_stale_scheduled_screenshots, _purge_workspace_retention
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
        workspace_id=membership.workspace_id,
        action="old.event",
        created_at=now - timedelta(days=31),
    )
    recent_log = AuditLog(
        workspace_id=membership.workspace_id,
        action="recent.event",
        created_at=now - timedelta(days=1),
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
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.workspace_id == membership.workspace_id)
            )
        )
        .scalars()
        .all()
    )
    assert {log.action for log in remaining_logs} == {"recent.event"}

    remaining_events = (
        (
            await db_session.execute(
                select(Event).where(Event.workspace_id == membership.workspace_id)
            )
        )
        .scalars()
        .all()
    )
    assert {e.session_id for e in remaining_events} == {"purge-s2"}


async def test_purge_stale_scheduled_screenshots_protects_pending_and_in_flight_dirs(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"purge-sched-{uuid.uuid4().hex[:8]}@example.com"
    )
    personas = await seed_baseline_personas(db_session, membership.workspace_id)

    config = ScheduledSimulation(
        workspace_id=membership.workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=personas[0].id,
        interval=ScheduleInterval.ON_PUSH,
    )
    db_session.add(config)
    await db_session.flush()

    config_root = tmp_path / "uploads" / "scheduled" / str(config.id)
    pending_dir = config_root / "pending-not-yet-fired"
    in_flight_dir = config_root / "in-flight-run"
    old_orphan_dir = config_root / "old-orphan"
    recent_orphan_dir = config_root / "recent-orphan"
    for d in (pending_dir, in_flight_dir, old_orphan_dir, recent_orphan_dir):
        d.mkdir(parents=True)

    config.pending_screenshots_dir = str(pending_dir)
    db_session.add(config)

    run = SimulationRun(
        workspace_id=membership.workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=personas[0].id,
        screenshots_dir=str(in_flight_dir),
        status=RunStatus.RUNNING,
        scheduled_simulation_id=config.id,
    )
    db_session.add(run)
    await db_session.commit()

    old_time = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    os.utime(old_orphan_dir, (old_time, old_time))
    # pending_dir/in_flight_dir keep their fresh mtime -- they must survive
    # purely because they're referenced, not because they're recent.

    await _purge_stale_scheduled_screenshots(
        db_session, membership.workspace_id, retention_days=30, upload_dir=tmp_path / "uploads"
    )

    assert pending_dir.exists()
    assert in_flight_dir.exists()
    assert recent_orphan_dir.exists()
    assert not old_orphan_dir.exists()
