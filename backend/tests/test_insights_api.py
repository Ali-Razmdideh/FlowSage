from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from tests.conftest import create_api_key_for, create_workspace_and_admin

_T0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


async def _make_run(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    """Duplicated from `test_insights.py` on purpose -- this codebase's
    convention (see `test_calibration_api.py` vs `test_calibration.py`) is each
    test file keeps its own local fixtures rather than importing across
    `test_*.py` modules."""
    persona = Persona(
        workspace_id=workspace_id,
        slug=f"insights-api-persona-{uuid.uuid4().hex[:8]}",
        name="Insights API Test Persona",
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
    created_at: datetime | None = None,
) -> FrictionIssue:
    issue = FrictionIssue(
        workspace_id=workspace_id,
        run_id=run_id,
        screen="checkout",
        severity="high",
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


async def test_insights_funnel_requires_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/insights/funnel")

    assert response.status_code == 401


async def test_insights_friction_issues_requires_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/insights/friction-issues")

    assert response.status_code == 401


async def test_insights_funnel_returns_workspace_scoped_data(
    app: FastAPI, db_session: AsyncSession
) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-api-funnel-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key = await create_api_key_for(db_session, membership.workspace_id)
    db_session.add_all(
        [
            Event(
                workspace_id=membership.workspace_id,
                session_id="s1",
                screen="landing",
                event="screen_view",
                timestamp=_T0,
                device="mobile",
                cohort="paid_users",
            ),
            Event(
                workspace_id=membership.workspace_id,
                session_id="s1",
                screen="checkout",
                event="screen_view",
                timestamp=_T0 + timedelta(minutes=1),
                device="mobile",
                cohort="paid_users",
            ),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/insights/funnel", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    body = response.json()
    assert body["total_sessions"] == 1
    assert {step["screen"] for step in body["funnel"]} == {"landing", "checkout"}


async def test_insights_friction_issues_returns_workspace_scoped_data(
    app: FastAPI, db_session: AsyncSession
) -> None:
    _, membership_a = await create_workspace_and_admin(
        db_session, f"insights-api-issues-a-{uuid.uuid4().hex[:8]}@example.com"
    )
    _, membership_b = await create_workspace_and_admin(
        db_session, f"insights-api-issues-b-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key_a = await create_api_key_for(db_session, membership_a.workspace_id)
    run_a = await _make_run(db_session, membership_a.workspace_id)
    run_b = await _make_run(db_session, membership_b.workspace_id)
    await _make_issue(db_session, membership_a.workspace_id, run_a)
    await _make_issue(db_session, membership_b.workspace_id, run_b)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/insights/friction-issues", headers={"X-API-Key": api_key_a}
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["issues"]) == 1
    assert body["next_cursor"] is None


async def test_insights_friction_issues_rejects_malformed_cursor(
    app: FastAPI, db_session: AsyncSession
) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-api-badcursor-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key = await create_api_key_for(db_session, membership.workspace_id)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/insights/friction-issues",
            params={"cursor": "not-a-valid-cursor"},
            headers={"X-API-Key": api_key},
        )

    assert response.status_code == 400


async def test_insights_friction_issues_paginates(app: FastAPI, db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-api-page-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key = await create_api_key_for(db_session, membership.workspace_id)
    run_id = await _make_run(db_session, membership.workspace_id)
    for i in range(3):
        await _make_issue(
            db_session, membership.workspace_id, run_id, created_at=_T0 + timedelta(minutes=i)
        )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(
            "/v1/insights/friction-issues",
            params={"limit": 2},
            headers={"X-API-Key": api_key},
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["issues"]) == 2
        assert first_body["next_cursor"] is not None

        second = await client.get(
            "/v1/insights/friction-issues",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
            headers={"X-API-Key": api_key},
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["issues"]) == 1
        assert second_body["next_cursor"] is None
