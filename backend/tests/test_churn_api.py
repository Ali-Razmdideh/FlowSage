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
    await upsert_user(db_session, "churn-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "churn-api@example.com", "password": "hunter2"}
        )
        yield client


async def test_cohorts_compare_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/graph/cohorts/compare")

    assert response.status_code == 401


async def test_churn_risk_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/graph/churn-risk")

    assert response.status_code == 401


async def test_node_intelligence_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/graph/nodes/checkout")

    assert response.status_code == 401


async def test_cohorts_compare_auto_discovers_and_ranks_screens(
    app: FastAPI, db_session: AsyncSession
) -> None:
    api_key = app.state.settings.events_api_key
    # Namespaced session ids so this test's rows don't collide with other test
    # files' own "s{n}"-style ids in the shared, un-truncated events table.
    # Both cohorts walk landing -> cart -> checkout; "paid" never drops at
    # cart, "trial" drops half the time -- mirrors test_events.py's own
    # drop-off fixture shape so cart isn't the funnel's terminal screen
    # (a terminal screen's drop_off_rate is always 0, regardless of cohort).
    session_ids = [f"churn-cmp-{i}" for i in range(8)]
    paid_ids, trial_ids = session_ids[:4], session_ids[4:]
    events = [
        *[_event(sid, "landing", 0, "paid") for sid in paid_ids],
        *[_event(sid, "cart", 1, "paid") for sid in paid_ids],
        *[_event(sid, "checkout", 2, "paid") for sid in paid_ids],
        *[_event(sid, "landing", 0, "trial") for sid in trial_ids],
        *[_event(sid, "cart", 1, "trial") for sid in trial_ids],
        *[_event(sid, "checkout", 2, "trial") for sid in trial_ids[:2]],
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.get(
                "/graph/cohorts/compare", params={"cohorts": ["paid", "trial"]}
            )

        assert response.status_code == 200
        body = response.json()
        cohorts_seen = {c["cohort"] for c in body["cohorts"]}
        assert {"paid", "trial"} <= cohorts_seen
        cart_screen = next(s for s in body["screens"] if s["screen"] == "cart")
        assert cart_screen["drop_off_by_cohort"]["paid"] == 0.0
        assert cart_screen["drop_off_by_cohort"]["trial"] == 0.5
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_churn_risk_ranks_segments_by_risk_score(
    app: FastAPI, db_session: AsyncSession
) -> None:
    api_key = app.state.settings.events_api_key
    session_ids = [f"churn-risk-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0, "healthy") for i in range(2)],
        *[_event(session_ids[i], "checkout", 1, "healthy") for i in range(2)],
        *[_event(session_ids[i], "landing", 0, "at_risk") for i in range(2, 4)],
        _event(session_ids[2], "checkout", 1, "at_risk"),
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.get("/graph/churn-risk")

        assert response.status_code == 200
        body = response.json()
        by_cohort = {s["cohort"]: s for s in body}
        assert by_cohort["at_risk"]["risk_score"] > by_cohort["healthy"]["risk_score"]
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_node_intelligence_returns_404_for_unknown_screen(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.get("/graph/nodes/nonexistent_screen_xyz")

    assert response.status_code == 404


async def test_node_intelligence_returns_recommendations_for_friction_screen(
    app: FastAPI, db_session: AsyncSession
) -> None:
    api_key = app.state.settings.events_api_key
    session_ids = [f"node-intel-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0, "paid") for i in range(4)],
        *[_event(session_ids[i], "checkout", 1, "paid") for i in range(4)],
        _event(session_ids[0], "confirmation", 2, "paid"),
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.get("/graph/nodes/checkout")

        assert response.status_code == 200
        body = response.json()
        assert body["screen"] == "checkout"
        assert body["drop_off_rate"] > 0.5
        assert len(body["recommendations"]) > 0
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
