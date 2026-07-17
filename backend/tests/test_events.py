from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from neo4j import GraphDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.seed import upsert_user

_T0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


def _event(
    session_id: str, screen: str, minutes: int, event: str = "screen_view"
) -> dict[str, str]:
    return {
        "session_id": session_id,
        "screen": screen,
        "event": event,
        "timestamp": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "device": "mobile",
        "cohort": "paid_users",
    }


async def test_ingest_requires_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/events", json=[_event("s1", "landing", 0)])

    assert response.status_code == 401


async def test_ingest_rejects_wrong_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events",
            json=[_event("s1", "landing", 0)],
            headers={"X-API-Key": "wrong-key"},
        )

    assert response.status_code == 401


async def test_ingest_stores_events_in_postgres(app: FastAPI, db_session: AsyncSession) -> None:
    api_key = app.state.settings.events_api_key
    events = [_event("s1", "landing", 0), _event("s1", "cart", 1)]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/events", json=events, headers={"X-API-Key": api_key})

    assert response.status_code == 201
    assert response.json() == {"ingested": 2}

    result = await db_session.execute(select(Event).where(Event.session_id == "s1"))
    rows = result.scalars().all()
    assert {r.screen for r in rows} == {"landing", "cart"}


async def test_ingest_continues_when_neo4j_unreachable(app: FastAPI) -> None:
    """The default `app` fixture points at an unreachable Neo4j -- ingestion into
    Postgres must still succeed (best-effort mirroring, matching flowsage-graph's
    own CLI resilience pattern)."""
    api_key = app.state.settings.events_api_key

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events", json=[_event("s1", "landing", 0)], headers={"X-API-Key": api_key}
        )

    assert response.status_code == 201


async def test_ingest_actually_writes_to_neo4j(
    app_with_real_neo4j: FastAPI, neo4j_credentials: tuple[str, str, str]
) -> None:
    api_key = app_with_real_neo4j.state.settings.events_api_key
    events = [_event("s1", "landing", 0), _event("s1", "cart", 1)]

    async with AsyncClient(
        transport=ASGITransport(app=app_with_real_neo4j), base_url="http://test"
    ) as client:
        response = await client.post("/v1/events", json=events, headers={"X-API-Key": api_key})

    assert response.status_code == 201

    uri, user, password = neo4j_credentials
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        record = session.run(
            "MATCH (a:Screen {name: 'landing'})-[t:TRANSITION]->(b:Screen {name: 'cart'}) "
            "RETURN t.session_id AS session_id"
        ).single()
    driver.close()

    assert record is not None
    assert record["session_id"] == "s1"


async def test_funnel_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/graph/funnel")

    assert response.status_code == 401


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "events-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "events-api@example.com", "password": "hunter2"}
        )
        yield client


async def test_funnel_discovers_path_and_friction(app: FastAPI, db_session: AsyncSession) -> None:
    api_key = app.state.settings.events_api_key
    events = [
        *[_event(f"s{i}", "landing", 0) for i in range(4)],
        *[_event(f"s{i}", "cart", 1) for i in range(4)],
        *[_event(f"s{i}", "checkout", 2) for i in range(2)],  # 2 of 4 drop off at cart
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ingest_response = await client.post(
            "/v1/events", json=events, headers={"X-API-Key": api_key}
        )
        assert ingest_response.status_code == 201

    async with _authed_client(app, db_session) as client:
        response = await client.get("/graph/funnel")

    assert response.status_code == 200
    body = response.json()
    assert body["total_sessions"] == 4
    screens = [step["screen"] for step in body["funnel"]]
    assert screens == ["landing", "cart", "checkout"]
    cart_step = next(s for s in body["funnel"] if s["screen"] == "cart")
    assert cart_step["drop_off_rate"] == 0.5


async def test_funnel_filters_by_cohort(app: FastAPI, db_session: AsyncSession) -> None:
    api_key = app.state.settings.events_api_key
    paid = _event("s1", "landing", 0)
    free = {**_event("s2", "landing", 0), "cohort": "free_trial"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/events", json=[paid, free], headers={"X-API-Key": api_key})

    async with _authed_client(app, db_session) as client:
        response = await client.get("/graph/funnel", params={"cohort": "free_trial"})

    assert response.status_code == 200
    assert response.json()["total_sessions"] == 1
