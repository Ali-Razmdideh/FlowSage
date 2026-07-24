"""Cross-tenant isolation regression tests: a workspace must never see another
workspace's personas, simulation runs, events, or friction issues. The single
most important test file in this chunk."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def test_persona_created_in_one_workspace_is_invisible_in_another(
    app: FastAPI, db_session: AsyncSession
) -> None:
    tenant_a_email = f"isolation-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-b-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    slug = f"isolation-persona-{uuid.uuid4().hex[:8]}"
    async with _authed_client(app, tenant_a_email) as client_a:
        create_response = await client_a.post(
            "/personas",
            json={
                "slug": slug,
                "name": "Tenant A Persona",
                "description": "d",
                "tech_affinity": "Low",
                "primary_device": "Desktop",
                "discovery_mode": "Search",
                "contextual_triggers": [],
                "technical_literacy": 0.5,
                "anxiety": 0.5,
                "patience": 0.5,
                "curiosity": 0.5,
            },
        )
        assert create_response.status_code == 201
        persona_id = create_response.json()["id"]

    async with _authed_client(app, tenant_b_email) as client_b:
        list_response = await client_b.get("/personas")
        get_response = await client_b.get(f"/personas/{persona_id}")

    assert all(p["slug"] != slug for p in list_response.json())
    assert get_response.status_code == 404


async def test_friction_issue_export_is_workspace_scoped(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A FrictionIssue id from workspace A must 404 for a workspace B caller,
    not leak via the direct-by-id export endpoints."""
    import datetime

    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
    from flowsage_backend.models.workspace import Membership

    tenant_a_email = f"isolation-fi-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-fi-b-{uuid.uuid4().hex[:8]}@example.com"
    user_a = await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    from sqlalchemy import select

    membership_a = (
        await db_session.execute(select(Membership).where(Membership.user_id == user_a.id))
    ).scalar_one()

    persona = Persona(
        workspace_id=membership_a.workspace_id,
        slug=f"fi-persona-{uuid.uuid4().hex[:8]}",
        name="P",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Desktop",
        discovery_mode="Search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.flush()

    run = SimulationRun(
        workspace_id=membership_a.workspace_id,
        flow_name="f",
        goal="g",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()

    issue = FrictionIssue(
        workspace_id=membership_a.workspace_id,
        run_id=run.id,
        screen="checkout",
        severity="high",
        title="t",
        heuristic_violated="h",
        persona_impact="p",
        description="d",
        suggested_fix="f",
    )
    db_session.add(issue)
    await db_session.commit()
    await db_session.refresh(issue)

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.post(f"/friction-issues/{issue.id}/export/slack")

    assert response.status_code == 404


async def test_retraining_rejects_persona_from_another_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A workspace B caller must not be able to start a retraining job (which
    mutates persona sliders and writes a PersonaMemory) against a persona_id
    that belongs to workspace A."""
    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.workspace import Membership

    from sqlalchemy import select

    tenant_a_email = f"isolation-retrain-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-retrain-b-{uuid.uuid4().hex[:8]}@example.com"
    user_a = await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    membership_a = (
        await db_session.execute(select(Membership).where(Membership.user_id == user_a.id))
    ).scalar_one()

    persona = Persona(
        workspace_id=membership_a.workspace_id,
        slug=f"retrain-persona-{uuid.uuid4().hex[:8]}",
        name="P",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Desktop",
        discovery_mode="Search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.post("/calibration/retrain", json={"persona_id": str(persona.id)})

    assert response.status_code == 422


async def test_create_simulation_rejects_persona_from_another_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A workspace B caller must not be able to start a simulation run against
    a persona_id that belongs to workspace A."""
    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.workspace import Membership

    from sqlalchemy import select

    tenant_a_email = f"isolation-sim-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-sim-b-{uuid.uuid4().hex[:8]}@example.com"
    user_a = await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    membership_a = (
        await db_session.execute(select(Membership).where(Membership.user_id == user_a.id))
    ).scalar_one()

    persona = Persona(
        workspace_id=membership_a.workspace_id,
        slug=f"sim-persona-{uuid.uuid4().hex[:8]}",
        name="P",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Desktop",
        discovery_mode="Search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.post(
            "/simulations",
            data={"persona_id": str(persona.id), "goal": "goal", "flow_name": "flow"},
            files={"files": ("01.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )

    assert response.status_code == 422


async def test_api_key_created_in_one_workspace_does_not_authenticate_for_another(
    app: FastAPI, db_session: AsyncSession
) -> None:
    tenant_a_email = f"isolation-key-a-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_a_email, "hunter2")

    async with _authed_client(app, tenant_a_email) as client_a:
        create = await client_a.post("/settings/integrations/api-keys", json={"name": "tenant-a-key"})
    raw_key = create.json()["key"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events",
            json=[
                {
                    "session_id": "isolation-key-check",
                    "screen": "landing",
                    "event": "screen_view",
                    "timestamp": "2026-07-23T00:00:00Z",
                }
            ],
            headers={"X-API-Key": raw_key},
        )

    assert response.status_code == 201
    # The event landed in tenant A's workspace specifically -- confirm via a second
    # tenant's authenticated session never seeing it.
    tenant_b_email = f"isolation-key-b-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_b_email, "hunter2")
    async with _authed_client(app, tenant_b_email) as client_b:
        funnel = await client_b.get("/graph/funnel")
    assert funnel.json()["total_sessions"] == 0


async def test_webhook_deliveries_do_not_leak_across_workspaces(
    app: FastAPI, db_session: AsyncSession
) -> None:
    tenant_a_email = f"isolation-webhook-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-webhook-b-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    async with _authed_client(app, tenant_a_email) as client_a:
        create_a = await client_a.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/a", "event_types": ["alert.triggered"]},
        )
    webhook_a_id = create_a.json()["id"]

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.get(f"/settings/integrations/webhooks/{webhook_a_id}/deliveries")

    assert response.status_code == 404


async def test_audit_log_is_workspace_scoped(app: FastAPI, db_session: AsyncSession) -> None:
    from flowsage_backend.audit import record_audit_event
    from tests.conftest import create_workspace_and_admin

    tenant_a_email = f"isolation-audit-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-audit-b-{uuid.uuid4().hex[:8]}@example.com"
    user_a, membership_a = await create_workspace_and_admin(db_session, tenant_a_email)
    await create_workspace_and_admin(db_session, tenant_b_email)

    await record_audit_event(
        db_session, membership_a.workspace_id, actor_user_id=user_a.id, action="auth.login"
    )

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.get("/audit-logs")

    assert response.status_code == 200
    # Tenant B's own /auth/login just recorded its own entry -- the assertion is
    # that tenant A's entry never leaks in, not that the list is empty.
    assert all(e["actor_user_id"] != str(user_a.id) for e in response.json()["entries"])
