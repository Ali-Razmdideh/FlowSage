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
    await upsert_user(db_session, "alerts-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "alerts-api@example.com", "password": "hunter2"}
        )
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
    api_key = app.state.settings.events_api_key
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
    assert app.state.settings.slack_webhook_url is None
    async with _authed_client(app, db_session) as client:
        response = await client.post("/alerts/digest/run")

    assert response.status_code == 400
    assert "not configured" in response.json()["detail"].lower()
