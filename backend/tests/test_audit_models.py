from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog
from flowsage_backend.models.workspace import Workspace


async def test_audit_log_round_trips(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Audit Test", slug=f"audit-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)

    entry = AuditLog(
        workspace_id=workspace.id,
        actor_user_id=None,
        action="member.role_changed",
        target_type="membership",
        target_id=str(uuid.uuid4()),
        extra_data={"from_role": "viewer", "to_role": "admin"},
        ip_address="203.0.113.7",
    )
    db_session.add(entry)
    await db_session.commit()

    result = await db_session.execute(select(AuditLog).where(AuditLog.workspace_id == workspace.id))
    fetched = result.scalar_one()
    assert fetched.action == "member.role_changed"
    assert fetched.extra_data == {"from_role": "viewer", "to_role": "admin"}
    assert fetched.actor_user_id is None


async def test_audit_log_extra_data_defaults_to_empty_dict(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Audit Default", slug=f"audit-default-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)

    entry = AuditLog(workspace_id=workspace.id, action="auth.login")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)
    assert entry.extra_data == {}
    assert entry.target_type is None
