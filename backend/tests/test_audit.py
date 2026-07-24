from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import list_audit_logs, record_audit_event
from tests.conftest import create_workspace_and_admin


async def test_record_and_list_audit_event(db_session: AsyncSession) -> None:
    user, membership = await create_workspace_and_admin(
        db_session, f"audit-{uuid.uuid4().hex[:8]}@example.com"
    )

    await record_audit_event(
        db_session,
        membership.workspace_id,
        actor_user_id=user.id,
        action="auth.login",
        ip_address="203.0.113.7",
    )

    entries, next_cursor = await list_audit_logs(db_session, membership.workspace_id)
    assert len(entries) == 1
    assert entries[0].action == "auth.login"
    assert entries[0].actor_user_id == user.id
    assert next_cursor is None


async def test_list_audit_logs_filters_by_action(db_session: AsyncSession) -> None:
    user, membership = await create_workspace_and_admin(
        db_session, f"audit-filter-{uuid.uuid4().hex[:8]}@example.com"
    )
    await record_audit_event(db_session, membership.workspace_id, actor_user_id=user.id, action="auth.login")
    await record_audit_event(
        db_session, membership.workspace_id, actor_user_id=user.id, action="member.role_changed"
    )

    entries, _ = await list_audit_logs(db_session, membership.workspace_id, action="auth.login")
    assert len(entries) == 1
    assert entries[0].action == "auth.login"


async def test_list_audit_logs_paginates_with_cursor(db_session: AsyncSession) -> None:
    user, membership = await create_workspace_and_admin(
        db_session, f"audit-page-{uuid.uuid4().hex[:8]}@example.com"
    )
    for i in range(3):
        await record_audit_event(
            db_session, membership.workspace_id, actor_user_id=user.id, action=f"test.event.{i}"
        )

    page_one, cursor = await list_audit_logs(db_session, membership.workspace_id, limit=2)
    assert len(page_one) == 2
    assert cursor is not None

    page_two, cursor_two = await list_audit_logs(
        db_session, membership.workspace_id, limit=2, cursor=cursor
    )
    assert len(page_two) == 1
    assert cursor_two is None
    assert {e.id for e in page_one} & {e.id for e in page_two} == set()


async def test_record_audit_event_never_raises_on_bad_input(db_session: AsyncSession) -> None:
    """Passing a nonexistent workspace_id violates the FK -- this must be swallowed,
    not propagated, per the spec's best-effort audit-write requirement."""
    await record_audit_event(db_session, uuid.uuid4(), action="auth.login")
