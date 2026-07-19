import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "exports-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "exports-api@example.com", "password": "hunter2"}
        )
        yield client


async def _make_issue(db_session: AsyncSession) -> uuid.UUID:
    persona = Persona(
        slug=f"exports-persona-{uuid.uuid4().hex[:8]}",
        name="Exports Test Persona",
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
    db_session.add(persona)
    await db_session.flush()

    run = SimulationRun(
        flow_name="checkout",
        goal="buy",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()

    issue = FrictionIssue(
        run_id=run.id,
        screen="checkout",
        severity="high",
        title="Confusing CTA",
        heuristic_violated="Visibility of system status",
        persona_impact="Anxious users abandon.",
        description="The primary button is unlabeled.",
        suggested_fix="Add a clear label.",
    )
    db_session.add(issue)
    await db_session.commit()
    return issue.id


async def test_export_issue_to_slack_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/friction-issues/{uuid.uuid4()}/export/slack")

    assert response.status_code == 401


async def test_export_issue_to_slack_returns_404_for_unknown_issue(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post(f"/friction-issues/{uuid.uuid4()}/export/slack")

    assert response.status_code == 404


async def test_export_issue_to_slack_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    issue_id = await _make_issue(db_session)
    assert app.state.settings.slack_webhook_url is None

    async with _authed_client(app, db_session) as client:
        response = await client.post(f"/friction-issues/{issue_id}/export/slack")

    assert response.status_code == 400


async def test_export_issue_to_jira_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    issue_id = await _make_issue(db_session)

    async with _authed_client(app, db_session) as client:
        response = await client.post(f"/friction-issues/{issue_id}/export/jira")

    assert response.status_code == 400
