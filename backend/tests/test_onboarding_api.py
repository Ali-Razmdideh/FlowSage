from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import AuditLog
from flowsage_backend.models.workspace import Membership
from flowsage_backend.onboarding import get_onboarding_status
from flowsage_backend.seed import seed_baseline_personas, upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def _onboarding_workspace_id(db_session: AsyncSession, email: str) -> uuid.UUID:
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def test_onboarding_status_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/onboarding/status")

    assert response.status_code == 401


async def test_onboarding_status_all_false_for_fresh_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-api-{uuid.uuid4().hex[:8]}@example.com"
    await _onboarding_workspace_id(db_session, email)

    async with _authed_client(app, email) as client:
        response = await client.get("/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "has_api_key": False,
        "has_events": False,
        "has_completed_simulation": False,
        "has_multiple_members": False,
    }


async def test_import_sample_data_endpoint_ingests_and_creates_run(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-import-{uuid.uuid4().hex[:8]}@example.com"
    workspace_id = await _onboarding_workspace_id(db_session, email)
    await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, email) as client:
        response = await client.post("/onboarding/import-sample-data")

    assert response.status_code == 201
    body = response.json()
    assert body["events_ingested"] == 44
    assert uuid.UUID(body["run_id"])


async def test_import_sample_data_endpoint_records_audit_event(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-import-audit-{uuid.uuid4().hex[:8]}@example.com"
    workspace_id = await _onboarding_workspace_id(db_session, email)
    await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, email) as client:
        response = await client.post("/onboarding/import-sample-data")
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action == "onboarding.sample_data_imported",
        )
    )
    entry = result.scalar_one()
    assert entry.target_id == run_id


async def test_import_sample_data_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/onboarding/import-sample-data")

    assert response.status_code == 401


async def test_import_sample_data_rejects_workspace_without_novice_persona(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-nopersona-{uuid.uuid4().hex[:8]}@example.com"
    await _onboarding_workspace_id(db_session, email)
    # deliberately skip seed_baseline_personas

    async with _authed_client(app, email) as client:
        response = await client.post("/onboarding/import-sample-data")

    assert response.status_code == 422


async def test_import_sample_data_is_workspace_scoped(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Cross-tenant isolation: importing into workspace A must not affect
    workspace B's onboarding status or event count."""
    email_a = f"onb-isolation-a-{uuid.uuid4().hex[:8]}@example.com"
    email_b = f"onb-isolation-b-{uuid.uuid4().hex[:8]}@example.com"
    workspace_a = await _onboarding_workspace_id(db_session, email_a)
    workspace_b = await _onboarding_workspace_id(db_session, email_b)
    await seed_baseline_personas(db_session, workspace_a)

    async with _authed_client(app, email_a) as client_a:
        import_response = await client_a.post("/onboarding/import-sample-data")
    assert import_response.status_code == 201

    async with _authed_client(app, email_b) as client_b:
        status_response = await client_b.get("/onboarding/status")

    assert status_response.json()["has_events"] is False
    other_status = await get_onboarding_status(db_session, workspace_b)
    assert other_status.has_events is False
