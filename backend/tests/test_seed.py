from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.workspace import Membership, Workspace
from flowsage_backend.security import verify_password
from flowsage_backend.seed import upsert_user


async def test_upsert_user_creates_new_user(db_session: AsyncSession) -> None:
    user = await upsert_user(db_session, "new-user@example.com", "hunter2")

    assert user.email == "new-user@example.com"
    assert verify_password("hunter2", user.hashed_password)


async def test_upsert_user_resets_password_for_existing_user(db_session: AsyncSession) -> None:
    first = await upsert_user(db_session, "reset-me@example.com", "old-password")
    second = await upsert_user(db_session, "reset-me@example.com", "new-password")

    assert first.id == second.id
    assert verify_password("new-password", second.hashed_password)
    assert not verify_password("old-password", second.hashed_password)


async def test_find_seedable_workspace_skips_memberless_workspaces(
    db_session: AsyncSession,
) -> None:
    """Reproduces the fresh-install bug: the `e463496b1d0f_backfill_default_workspace`
    migration unconditionally creates an empty "Default" workspace before any real
    user exists, so it's always the oldest workspace on a fresh database. A naive
    "oldest workspace" query picks it, leaving the real e2e/onboarding user's
    workspace without baseline personas. `_find_seedable_workspace` must skip
    workspaces with no `Membership` row."""
    from flowsage_backend.__main__ import _find_seedable_workspace

    # This suite's `db_session` fixture shares one Postgres container/database
    # across every test in the run (see conftest.py), and other tests in this
    # same file create their own real (member-having) workspaces via
    # `upsert_user`. Clear existing workspaces first (cascades to memberships
    # via `ondelete="CASCADE"`) so the "oldest workspace" comparison below is
    # deterministic regardless of test execution order.
    await db_session.execute(delete(Workspace))
    await db_session.commit()

    phantom = Workspace(name="Default", slug="fs-default-test")
    db_session.add(phantom)
    await db_session.flush()

    # A real workspace, created later, but WITH a membership -- must still win.
    # `upsert_user` is exactly the function the `create-user` CLI command calls,
    # and (per `seed.py`) it bootstraps a new user with its own personal
    # workspace + admin membership -- the same real-world path that leaves the
    # migration's phantom "Default" workspace stranded without members.
    user = await upsert_user(db_session, "seed-personas-test@example.com", "hunter2")
    membership_result = await db_session.execute(
        select(Membership).where(Membership.user_id == user.id)
    )
    membership = membership_result.scalar_one()

    found = await _find_seedable_workspace(db_session)
    assert found is not None
    assert found.id == membership.workspace_id
