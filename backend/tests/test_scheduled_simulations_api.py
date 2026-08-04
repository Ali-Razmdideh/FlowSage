import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import (
    FrictionIssue,
    RunStatus,
    SimulationRun,
    SimulationStep,
)
from flowsage_backend.models.workspace import Membership, Workspace
from flowsage_backend.scheduled_simulations import fire_due_scheduled_simulations
from flowsage_backend.seed import seed_baseline_personas, upsert_user

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-but-good-enough-for-a-suffix-check"


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "sched-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "sched-api@example.com", "password": "hunter2"}
        )
        yield client


async def _sched_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    from sqlalchemy import select

    user = await upsert_user(db_session, "sched-api@example.com", "hunter2")
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


async def test_create_scheduled_simulation_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/scheduled-simulations", json={})
    assert response.status_code == 401


async def test_create_list_update_delete_scheduled_simulation(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(persona.id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "daily",
            },
        )
        assert create_response.status_code == 201
        config = create_response.json()
        assert config["flow_name"] == "Checkout"
        assert config["active"] is True
        assert config["has_pending_screenshots"] is False

        list_response = await client.get("/scheduled-simulations")
        assert list_response.status_code == 200
        assert any(c["id"] == config["id"] for c in list_response.json())

        update_response = await client.patch(
            f"/scheduled-simulations/{config['id']}", json={"active": False}
        )
        assert update_response.status_code == 200
        assert update_response.json()["active"] is False

        delete_response = await client.delete(f"/scheduled-simulations/{config['id']}")
        assert delete_response.status_code == 204

        list_after_delete = await client.get("/scheduled-simulations")
        assert all(c["id"] != config["id"] for c in list_after_delete.json())


async def test_create_scheduled_simulation_rejects_unknown_persona(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(uuid.uuid4()),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "daily",
            },
        )
    assert response.status_code == 422


async def test_push_screenshots_stages_pending_set(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        push_response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )
        assert push_response.status_code == 200
        assert push_response.json()["has_pending_screenshots"] is True


async def test_push_screenshots_rejects_disallowed_file_type(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 422


async def _fetch_config(db_session: AsyncSession, config_id: str) -> ScheduledSimulation:
    # populate_existing=True forces a reload from the row this query just
    # fetched, overriding whatever this session's identity map already had
    # cached for this id -- needed here because the API requests in these
    # tests run against a *different* session (the app's own, via
    # get_db_session), so this session's cached copy can otherwise go stale
    # across a push/fire that happened through the API.
    result = await db_session.execute(
        select(ScheduledSimulation)
        .where(ScheduledSimulation.id == uuid.UUID(config_id))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def test_push_screenshots_bad_file_does_not_corrupt_previously_staged_set(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Regression test: a push with one bad-suffix file among good ones must
    422 without destroying an already-staged good set left by an earlier
    push (the old code `rmtree`d the shared directory before validating any
    file in the new batch)."""
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        first_push = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )
        assert first_push.status_code == 200
        assert first_push.json()["has_pending_screenshots"] is True

        config = await _fetch_config(db_session, config_id)
        first_dir = Path(config.pending_screenshots_dir)  # type: ignore[arg-type]
        assert (first_dir / "01_cart.png").exists()
        assert (first_dir / "01_cart.png").read_bytes() == _PNG_BYTES

        second_push = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files=[
                ("files", ("02_pay.png", _PNG_BYTES, "image/png")),
                ("files", ("notes.txt", b"hello", "text/plain")),
            ],
        )
        assert second_push.status_code == 422

    # The first push's directory and files must still be intact -- the bad
    # second push must not have touched them, and the config's pending
    # pointer must still point at the original (good) staged set.
    assert first_dir.exists()
    assert (first_dir / "01_cart.png").exists()
    assert (first_dir / "01_cart.png").read_bytes() == _PNG_BYTES

    refreshed = await _fetch_config(db_session, config_id)
    assert refreshed.pending_screenshots_dir == str(first_dir)


async def test_push_screenshots_uses_unique_dir_and_does_not_touch_fired_run(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Regression test: successive pushes to the same config must land in
    different directories, and once a push's directory has been consumed by
    a fired run, a later push must never touch it (the old code reused one
    fixed directory per config, so a later push would `rmtree` a still-being-
    processed fired run's screenshots out from under it)."""
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        first_push = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )
        assert first_push.status_code == 200

    config = await _fetch_config(db_session, config_id)
    first_dir = Path(config.pending_screenshots_dir)  # type: ignore[arg-type]
    assert (first_dir / "01_cart.png").exists()

    # Fire it, exactly like the real cron job would: this consumes the
    # pending set as the run's permanent screenshots_dir and clears the
    # pointer. This shared "sched-api@example.com" workspace may also have
    # other configs left pending by earlier tests in this file, so filter
    # down to the run for *this* config rather than asserting on the total
    # count of everything fired.
    fired = await fire_due_scheduled_simulations(
        db_session, workspace_id, datetime.now(timezone.utc)
    )
    our_fired = [r for r in fired if str(r.scheduled_simulation_id) == config_id]
    assert len(our_fired) == 1
    assert our_fired[0].screenshots_dir == str(first_dir)

    async with _authed_client(app, db_session) as client:
        second_push = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("02_pay.png", _PNG_BYTES, "image/png")},
        )
        assert second_push.status_code == 200

    refreshed = await _fetch_config(db_session, config_id)
    second_dir = Path(refreshed.pending_screenshots_dir)  # type: ignore[arg-type]

    assert second_dir != first_dir
    # The fired run's directory (still its permanent screenshots_dir) must
    # be untouched by the second push.
    assert first_dir.exists()
    assert (first_dir / "01_cart.png").exists()
    assert (first_dir / "01_cart.png").read_bytes() == _PNG_BYTES
    assert (second_dir / "02_pay.png").exists()


async def test_concurrent_pushes_do_not_leak_a_staging_directory(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Regression test for the row-lock fix: two pushes to the same config
    fired concurrently must serialize (via SELECT ... FOR UPDATE) so the
    second one always reads the first one's already-committed directory as
    its `previous_pending_dir` and cleans it up -- not a stale None that
    would leave the first push's directory orphaned on disk forever."""
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

    # Two separate, already-authenticated clients logged in sequentially first
    # -- login itself does a DB write (upsert_user) against the shared
    # `db_session` fixture, and asyncpg's connection doesn't tolerate two
    # coroutines issuing queries on it at once, so that part must not run
    # concurrently. Only the actual pushes below run concurrently, and they
    # go through the app's own per-request session (via get_db_session), not
    # this fixture's session, so they don't hit that restriction.
    client_a = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await client_a.post(
        "/auth/login", json={"email": "sched-api@example.com", "password": "hunter2"}
    )
    await client_b.post(
        "/auth/login", json={"email": "sched-api@example.com", "password": "hunter2"}
    )

    async def _push(client: AsyncClient, filename: str) -> None:
        response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": (filename, _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 200

    try:
        await asyncio.gather(_push(client_a, "a.png"), _push(client_b, "b.png"))
    finally:
        await client_a.aclose()
        await client_b.aclose()

    config = await _fetch_config(db_session, config_id)
    final_dir = Path(config.pending_screenshots_dir)  # type: ignore[arg-type]
    remaining_dirs = list(final_dir.parent.iterdir())

    assert remaining_dirs == [final_dir]


async def test_delete_scheduled_simulation_removes_its_staged_screenshot_dirs(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Regression test: deleting a config used to leak every directory it
    ever staged -- the DB row (and thus the only pointer to that directory)
    was gone, but nothing on disk ever got cleaned up. Delete must now
    reclaim the whole `<upload_dir>/scheduled/<config_id>/` tree."""
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        push_response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )
        assert push_response.status_code == 200

    config = await _fetch_config(db_session, config_id)
    config_root = Path(config.pending_screenshots_dir).parent  # type: ignore[arg-type]
    assert config_root.exists()

    async with _authed_client(app, db_session) as client:
        delete_response = await client.delete(f"/scheduled-simulations/{config_id}")
        assert delete_response.status_code == 204

    assert not config_root.exists()


async def test_delete_scheduled_simulation_leaves_in_flight_run_dir_untouched(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Regression test: if a fired run is still QUEUED/RUNNING against a
    config, deleting that config must not rmtree the directory the worker
    is (or is about to be) reading -- the FK's ON DELETE SET NULL means the
    run survives the config's deletion and still needs its screenshots."""
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        push_response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )
        assert push_response.status_code == 200

    config = await _fetch_config(db_session, config_id)
    run_dir = Path(config.pending_screenshots_dir)  # type: ignore[arg-type]

    fired = await fire_due_scheduled_simulations(
        db_session, workspace_id, datetime.now(timezone.utc)
    )
    our_run = next(r for r in fired if str(r.scheduled_simulation_id) == config_id)
    assert our_run.status == RunStatus.QUEUED
    assert run_dir.exists()

    async with _authed_client(app, db_session) as client:
        delete_response = await client.delete(f"/scheduled-simulations/{config_id}")
        assert delete_response.status_code == 204

    assert run_dir.exists()
    assert (run_dir / "01_cart.png").exists()


async def test_get_trend_computes_score_from_completed_runs(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        interval=ScheduleInterval.ON_PUSH,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/irrelevant",
        status=RunStatus.COMPLETED,
        scheduled_simulation_id=config.id,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        SimulationStep(
            workspace_id=workspace_id,
            run_id=run.id,
            sequence=0,
            screen="cart",
            action="a",
            reasoning="r",
        )
    )
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cart",
            severity="high",
            title="t",
            heuristic_violated="h",
            persona_impact="p",
            description="d",
            suggested_fix="f",
        )
    )
    await db_session.commit()

    async with _authed_client(app, db_session) as client:
        response = await client.get(f"/scheduled-simulations/{config.id}/trend")

    assert response.status_code == 200
    points = response.json()
    assert len(points) == 1
    assert points[0]["run_id"] == str(run.id)
    assert points[0]["score"] == 0.7
    assert points[0]["issue_count"] == 1


async def test_scheduled_simulations_isolate_by_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    other_workspace_id = await _create_workspace(db_session)
    other_personas = await seed_baseline_personas(db_session, other_workspace_id)
    other_config = ScheduledSimulation(
        workspace_id=other_workspace_id,
        flow_name="Other Flow",
        goal="Do the other thing",
        persona_id=other_personas[0].id,
        interval=ScheduleInterval.WEEKLY,
    )
    db_session.add(other_config)
    await db_session.commit()
    await db_session.refresh(other_config)

    async with _authed_client(app, db_session) as client:
        list_response = await client.get("/scheduled-simulations")
        assert all(c["id"] != str(other_config.id) for c in list_response.json())

        get_response = await client.patch(
            f"/scheduled-simulations/{other_config.id}", json={"active": False}
        )
        assert get_response.status_code == 404
