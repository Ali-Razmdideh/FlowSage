from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.insights import list_friction_issues
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from tests.conftest import create_workspace_and_admin

_T0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


async def _make_run(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    persona = Persona(
        workspace_id=workspace_id,
        slug=f"insights-persona-{uuid.uuid4().hex[:8]}",
        name="Insights Test Persona",
        description="d",
        baseline=False,
        tech_affinity="medium",
        primary_device="desktop",
        discovery_mode="search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    session.add(persona)
    await session.flush()

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="checkout",
        goal="buy",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    return run.id


async def _make_issue(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    screen: str = "checkout",
    severity: str = "high",
    created_at: datetime | None = None,
) -> FrictionIssue:
    issue = FrictionIssue(
        workspace_id=workspace_id,
        run_id=run_id,
        screen=screen,
        severity=severity,
        title="Confusing CTA",
        heuristic_violated="Visibility of system status",
        persona_impact="Anxious users abandon.",
        description="The primary button is unlabeled.",
        suggested_fix="Add a clear label.",
    )
    session.add(issue)
    await session.flush()
    if created_at is not None:
        issue.created_at = created_at
        await session.flush()
    return issue


async def test_list_friction_issues_scopes_to_workspace(db_session: AsyncSession) -> None:
    _, membership_a = await create_workspace_and_admin(
        db_session, f"insights-a-{uuid.uuid4().hex[:8]}@example.com"
    )
    _, membership_b = await create_workspace_and_admin(
        db_session, f"insights-b-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_a = await _make_run(db_session, membership_a.workspace_id)
    run_b = await _make_run(db_session, membership_b.workspace_id)
    await _make_issue(db_session, membership_a.workspace_id, run_a)
    await _make_issue(db_session, membership_b.workspace_id, run_b)
    await db_session.commit()

    issues, cursor = await list_friction_issues(db_session, membership_a.workspace_id)
    assert len(issues) == 1
    assert issues[0].workspace_id == membership_a.workspace_id
    assert cursor is None


async def test_list_friction_issues_filters_by_severity_and_screen(
    db_session: AsyncSession,
) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-filter-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_id = await _make_run(db_session, membership.workspace_id)
    await _make_issue(
        db_session, membership.workspace_id, run_id, screen="checkout", severity="high"
    )
    await _make_issue(db_session, membership.workspace_id, run_id, screen="landing", severity="low")
    await db_session.commit()

    high_only, _ = await list_friction_issues(db_session, membership.workspace_id, severity="high")
    assert {i.screen for i in high_only} == {"checkout"}

    checkout_only, _ = await list_friction_issues(
        db_session, membership.workspace_id, screen="checkout"
    )
    assert {i.severity for i in checkout_only} == {"high"}


async def test_list_friction_issues_filters_by_since(db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-since-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_id = await _make_run(db_session, membership.workspace_id)
    await _make_issue(db_session, membership.workspace_id, run_id, created_at=_T0)
    await _make_issue(
        db_session, membership.workspace_id, run_id, created_at=_T0 + timedelta(days=1)
    )
    await db_session.commit()

    recent, _ = await list_friction_issues(
        db_session, membership.workspace_id, since=_T0 + timedelta(hours=12)
    )
    assert len(recent) == 1


async def test_list_friction_issues_paginates_with_cursor(db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-page-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_id = await _make_run(db_session, membership.workspace_id)
    for i in range(3):
        await _make_issue(
            db_session, membership.workspace_id, run_id, created_at=_T0 + timedelta(minutes=i)
        )
    await db_session.commit()

    page_one, cursor = await list_friction_issues(db_session, membership.workspace_id, limit=2)
    assert len(page_one) == 2
    assert cursor is not None

    page_two, cursor_two = await list_friction_issues(
        db_session, membership.workspace_id, limit=2, cursor=cursor
    )
    assert len(page_two) == 1
    assert cursor_two is None
    assert {i.id for i in page_one} & {i.id for i in page_two} == set()
