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


def _event(session_id: str, screen: str, minutes: int, cohort: str) -> dict[str, str]:
    return {
        "session_id": session_id,
        "screen": screen,
        "event": "screen_view",
        "timestamp": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "device": "mobile",
        "cohort": cohort,
    }


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login_to_default_workspace(client, db_session, "alerts-api@example.com")
        yield client


async def test_get_alerts_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/alerts")

    assert response.status_code == 401


async def test_get_alerts_flags_a_churn_risk_segment(
    app: FastAPI, db_session: AsyncSession
) -> None:
    # 8 sessions reach landing; only 2 continue to checkout, and only 1 of
    # those continues to confirmation. This yields a churn risk_score of
    # ~0.52 (drop-off 0.6-weighted + friction-density 0.4-weighted), clearing
    # alerts.CHURN_RISK_ALERT_THRESHOLD (0.5). A flatter 2-step funnel (e.g.
    # 4 landing / 1 checkout) tops out around 0.43 and never alerts, since a
    # terminal funnel step's drop-off rate is always 0 by construction.
    # `/v1/events` ingestion resolves the shared "fs-default" workspace at
    # request time (see api/events.py's `_default_workspace_id`); this test
    # runs before any other test in the suite has caused that row to be
    # created, so it must ensure it exists itself, not just via
    # `login_to_default_workspace` (which only runs after the ingest below).
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)
    session_ids = [f"alerts-api-{i}" for i in range(8)]
    events = [
        *[_event(session_ids[i], "landing", 0, "at_risk_alerts") for i in range(8)],
        _event(session_ids[0], "checkout", 1, "at_risk_alerts"),
        _event(session_ids[1], "checkout", 1, "at_risk_alerts"),
        _event(session_ids[0], "confirmation", 2, "at_risk_alerts"),
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.get("/alerts")

        assert response.status_code == 200
        body = response.json()
        cohorts = {a["cohort"] for a in body["churn_alerts"]}
        assert "at_risk_alerts" in cohorts
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_digest_run_returns_400_when_slack_not_configured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/alerts/digest/run")

    assert response.status_code == 400
    assert "not configured" in response.json()["detail"].lower()
