from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.seed import upsert_user

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
    await upsert_user(db_session, "node-export-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "node-export-api@example.com", "password": "hunter2"}
        )
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
    api_key = app.state.settings.events_api_key
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
    api_key = app.state.settings.events_api_key
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
