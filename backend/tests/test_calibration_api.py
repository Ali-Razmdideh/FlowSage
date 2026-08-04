import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowsage_backend import billing
from flowsage_backend.api.calibration import stream_retraining_events
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Workspace
from flowsage_backend.seed import seed_baseline_personas, upsert_user


async def _cal_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    """`cal-api@example.com`'s own workspace (bootstrapped by `upsert_user`) --
    resolved directly so direct-model-construction test setup can tag rows
    with the same workspace the `_authed_client` HTTP session will read from."""
    user = await upsert_user(db_session, "cal-api@example.com", "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def _create_workspace(db_session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return workspace.id


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "cal-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "cal-api@example.com", "password": "hunter2"}
        )
        yield client


async def test_get_calibration_report_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/calibration/report")

    assert response.status_code == 401


async def test_get_calibration_report_empty_when_no_runs(
    app: FastAPI, db_session: AsyncSession
) -> None:
    # This suite's Postgres fixture is session-scoped (see conftest.py) -- rows
    # from other tests/files persist across the whole run. `personas[-1]`
    # ("power_user") never receives a completed run anywhere in this suite, so
    # its absence is a reliable per-test signal even against shared state.
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    untouched = personas[-1]

    async with _authed_client(app, db_session) as client:
        response = await client.get("/calibration/report")

    assert response.status_code == 200
    body = response.json()
    assert all(p["persona_id"] != str(untouched.id) for p in body["personas"])


async def test_get_calibration_report_flags_anomaly(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    # Namespaced session ids so this test's rows don't collide with
    # test_events.py's own "s{n}"-style ids in the shared, un-truncated events
    # table -- see the cleanup at the end of this test.
    session_ids = [f"cal-report-{i}" for i in range(10)]

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cal_report_checkout",
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen="cal_report_checkout",
                event="view",
                timestamp=now,
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen="cal_report_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
        )
    )
    await db_session.commit()

    try:
        async with _authed_client(app, db_session) as client:
            response = await client.get("/calibration/report")

        assert response.status_code == 200
        body = response.json()
        assert body["has_anomaly"] is True
        persona_body = next(p for p in body["personas"] if p["persona_id"] == str(persona.id))
        assert persona_body["screens"][0]["anomaly"] is True
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_start_retraining_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/calibration/retrain", json={"persona_id": str(uuid.uuid4())})

    assert response.status_code == 401


async def test_start_retraining_rejects_unknown_persona(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/calibration/retrain", json={"persona_id": str(uuid.uuid4())})

    assert response.status_code == 422


async def test_start_and_get_retraining_job(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/calibration/retrain", json={"persona_id": str(persona.id)}
        )
        assert create_response.status_code == 201
        job_id = create_response.json()["id"]
        assert create_response.json()["status"] == "queued"

        get_response = await client.get(f"/calibration/retrain/{job_id}")

    assert get_response.status_code == 200
    assert get_response.json()["persona_id"] == str(persona.id)


async def test_get_unknown_retraining_job_returns_404(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.get(f"/calibration/retrain/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_stream_retraining_events_emits_progress_then_done(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    job = RetrainingJob(
        workspace_id=workspace_id, persona_id=personas[0].id, status=RetrainingStatus.RUNNING
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    events = stream_retraining_events(
        session_factory, workspace_id, job.id, poll_interval_seconds=0.01
    )

    first_frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: progress" in first_frame

    async with session_factory() as session:
        db_job = await session.get(RetrainingJob, job.id)
        assert db_job is not None
        db_job.status = RetrainingStatus.COMPLETED
        await session.commit()

    second_frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: progress" in second_frame

    third_frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: done" in third_frame
    assert '"status": "completed"' in third_frame

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()


async def test_stream_retraining_events_reports_unknown_job(db_session: AsyncSession) -> None:
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    events = stream_retraining_events(
        session_factory, uuid.uuid4(), uuid.uuid4(), poll_interval_seconds=0.01
    )

    frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: error" in frame

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()


async def test_calibration_report_narrative_is_none_before_any_generation(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    session_ids = [f"cal-narrative-report-{i}" for i in range(10)]

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cal_narrative_report_checkout",
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen="cal_narrative_report_checkout",
                event="view",
                timestamp=now,
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen="cal_narrative_report_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
        )
    )
    await db_session.commit()

    try:
        async with _authed_client(app, db_session) as client:
            response = await client.get("/calibration/report")

        assert response.status_code == 200
        body = response.json()
        persona_body = next(p for p in body["personas"] if p["persona_id"] == str(persona.id))
        assert persona_body["narrative"] is None
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def _seed_drop_off_events(
    db_session: AsyncSession, workspace_id: uuid.UUID, screen: str
) -> list[str]:
    """Enough real events on `screen` (and a `_confirmation` follow-up only
    one session reaches) to observe a drop-off far above the anomaly
    threshold. Mirrors test_get_calibration_report_flags_anomaly's fixture
    shape. Returns the session ids used, for cleanup."""
    session_ids = [f"{screen}-{i}" for i in range(10)]
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen=screen,
                event="view",
                timestamp=now,
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen=f"{screen}_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
        )
    )
    await db_session.commit()
    return session_ids


async def _seed_completed_run_with_issue(
    db_session: AsyncSession, workspace_id: uuid.UUID, persona_id: uuid.UUID, screen: str
) -> SimulationRun:
    """A completed run with one low-severity predicted issue on `screen`, with
    no events of its own -- pair with `_seed_drop_off_events` for the observed
    side. Kept separate so multiple personas can share one observed screen:
    `discover_funnel` picks a single global path per workspace starting from
    the most common entry screen, so two personas predicting issues on two
    *different*, mutually exclusive screen clusters would only ever have one
    of them actually appear in the discovered funnel."""
    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona_id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen=screen,
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    await db_session.commit()
    return run


async def _seed_anomalous_persona_run(
    db_session: AsyncSession, workspace_id: uuid.UUID, persona_id: uuid.UUID, screen: str
) -> list[str]:
    """A completed run + predicted issue + observed drop-off for a single
    persona on its own dedicated screen. Returns the session ids used, for
    cleanup."""
    await _seed_completed_run_with_issue(db_session, workspace_id, persona_id, screen)
    return await _seed_drop_off_events(db_session, workspace_id, screen)


async def test_get_calibration_report_enqueue_failure_does_not_fail_request(
    app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critical #1: a Redis outage (enqueue_job raising) must not turn this
    read-only GET into a 500 -- the route already has a computed-report
    fallback and must just log and keep serving it."""
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    session_ids = await _seed_anomalous_persona_run(
        db_session, workspace_id, persona.id, "cal_report_redis_outage_checkout"
    )

    async def _boom(*args: object, **kwargs: object) -> None:
        raise ConnectionError("simulated redis outage")

    try:
        monkeypatch.setattr(app.state.arq_pool, "enqueue_job", _boom)

        async with _authed_client(app, db_session) as client:
            response = await client.get("/calibration/report")

        assert response.status_code == 200
        body = response.json()
        assert body["has_anomaly"] is True
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_get_calibration_report_checks_narrative_budget_once(
    app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Important #4: has_narrative_budget must be called exactly once per
    request, not once per anomalous persona -- otherwise every iteration
    would issue a redundant DB round-trip for the same answer (and, since no
    GeneratedInsight row commits until an enqueued job actually completes, a
    workspace near its cap could overshoot it by enqueuing once per persona
    in a single request)."""
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona_a, persona_b = personas[0], personas[1]

    # Both personas predict an issue on the same observed screen: discover_funnel
    # picks a single global path per workspace (see _seed_completed_run_with_issue's
    # docstring), so two personas on two different, disjoint screen clusters would
    # only ever have one of them actually show up as anomalous.
    screen = "cal_report_budget_shared_checkout"
    await _seed_completed_run_with_issue(db_session, workspace_id, persona_a.id, screen)
    await _seed_completed_run_with_issue(db_session, workspace_id, persona_b.id, screen)
    session_ids = await _seed_drop_off_events(db_session, workspace_id, screen)

    calls = 0
    original_has_budget = billing.has_narrative_budget

    async def _spy(session: AsyncSession, ws_id: uuid.UUID) -> bool:
        nonlocal calls
        calls += 1
        return await original_has_budget(session, ws_id)

    monkeypatch.setattr(billing, "has_narrative_budget", _spy)

    try:
        async with _authed_client(app, db_session) as client:
            response = await client.get("/calibration/report")

        assert response.status_code == 200
        body = response.json()
        anomalous_ids = {str(persona_a.id), str(persona_b.id)}
        anomalous_personas = [
            p
            for p in body["personas"]
            if p["persona_id"] in anomalous_ids and any(s["anomaly"] for s in p["screens"])
        ]
        # Both personas' anomalies must actually be present, otherwise this
        # test wouldn't be exercising the per-persona loop at all.
        assert len(anomalous_personas) == 2
        assert calls == 1
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
