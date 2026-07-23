from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event

from .conftest import create_api_key_for, ensure_default_workspace, login_to_default_workspace

_T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _event(session_id: str, screen: str, minutes: int) -> dict[str, str]:
    return {
        "session_id": session_id,
        "screen": screen,
        "event": "screen_view",
        "timestamp": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "device": "mobile",
        "cohort": "node-export",
    }


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login_to_default_workspace(client, db_session, "node-export-api@example.com")
        yield client


async def test_export_node_to_slack_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/graph/nodes/checkout/export/slack")

    assert response.status_code == 401


async def test_export_node_to_slack_returns_404_for_unknown_screen(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/graph/nodes/nonexistent_screen_xyz/export/slack")

    assert response.status_code == 404


async def test_export_node_to_slack_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)
    session_ids = [f"node-export-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0) for i in range(4)],
        *[_event(session_ids[i], "checkout", 1) for i in range(4)],
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.post("/graph/nodes/checkout/export/slack")

        assert response.status_code == 400
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_export_node_to_jira_returns_400_when_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)
    session_ids = [f"node-export-jira-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0) for i in range(4)],
        *[_event(session_ids[i], "checkout", 1) for i in range(4)],
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.post("/graph/nodes/checkout/export/jira")

        assert response.status_code == 400
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
