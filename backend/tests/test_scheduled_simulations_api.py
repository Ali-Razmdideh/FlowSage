import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Workspace
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
