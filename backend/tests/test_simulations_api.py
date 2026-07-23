import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowsage_backend.api.simulations import stream_simulation_events
from flowsage_backend.models.simulation import RunStatus, SimulationRun, SimulationStep
from flowsage_backend.models.workspace import Membership, Workspace
from flowsage_backend.seed import seed_baseline_personas, upsert_user
from flowsage_backend.simulations import create_run

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-but-good-enough-for-a-suffix-check"


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "sim-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "sim-api@example.com", "password": "hunter2"}
        )
        yield client


async def _sim_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    user = await upsert_user(db_session, "sim-api@example.com", "hunter2")
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


async def test_create_simulation_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/simulations", data={}, files={})

    assert response.status_code == 401


async def test_create_simulation_rejects_unknown_persona(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post(
            "/simulations",
            data={
                "persona_id": str(uuid.uuid4()),
                "goal": "Complete purchase",
                "flow_name": "Checkout",
            },
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )

    assert response.status_code == 422


async def test_create_simulation_rejects_disallowed_file_type(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sim_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    async with _authed_client(app, db_session) as client:
        response = await client.post(
            "/simulations",
            data={"persona_id": str(persona.id), "goal": "goal", "flow_name": "flow"},
            files={"files": ("script.exe", b"not-an-image", "application/octet-stream")},
        )

    assert response.status_code == 422


async def test_create_simulation_sanitizes_path_traversal_filename(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sim_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    async with _authed_client(app, db_session) as client:
        response = await client.post(
            "/simulations",
            data={"persona_id": str(persona.id), "goal": "goal", "flow_name": "flow"},
            files={"files": ("../../../../etc/evil.png", _PNG_BYTES, "image/png")},
        )

    assert response.status_code == 201
    run_id = response.json()["id"]

    run_dir = Path(app.state.settings.upload_dir) / run_id
    assert (run_dir / "evil.png").exists()
    assert not (run_dir.parent.parent / "etc").exists()


async def test_create_and_get_simulation(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _sim_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = next(p for p in personas if p.slug == "novice")

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/simulations",
            data={
                "persona_id": str(persona.id),
                "goal": "Complete purchase",
                "flow_name": "Checkout",
            },
            files=[
                ("files", ("01_cart.png", _PNG_BYTES, "image/png")),
                ("files", ("02_shipping.png", _PNG_BYTES, "image/png")),
            ],
        )
        assert create_response.status_code == 201
        run_id = create_response.json()["id"]
        assert create_response.json()["status"] == "queued"

        get_response = await client.get(f"/simulations/{run_id}")

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["flow_name"] == "Checkout"
    assert body["steps"] == []
    assert body["issues"] == []


async def test_get_unknown_simulation_returns_404(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.get(f"/simulations/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_stream_simulation_events_emits_steps_then_done(
    db_session: AsyncSession, tmp_path: "Path"
) -> None:
    screenshots_dir = tmp_path / "screens"
    screenshots_dir.mkdir()
    (screenshots_dir / "01.png").write_bytes(_PNG_BYTES)

    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    run = await create_run(
        db_session,
        workspace_id=workspace_id,
        persona_id=persona.id,
        flow_name="Checkout",
        goal="goal",
        screenshots_dir=screenshots_dir,
    )

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    events = stream_simulation_events(
        session_factory, workspace_id, run.id, poll_interval_seconds=0.01
    )

    # Add a step, then mark the run completed; the generator should surface both.
    async with session_factory() as session:
        db_run = await session.get(SimulationRun, run.id)
        assert db_run is not None
        session.add(
            SimulationStep(
                workspace_id=workspace_id,
                run_id=run.id,
                sequence=0,
                screen="01",
                action="looked at cart",
                reasoning="r",
            )
        )
        db_run.status = RunStatus.COMPLETED
        await session.commit()

    first_frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: step" in first_frame
    assert '"screen": "01"' in first_frame

    second_frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: done" in second_frame
    assert '"status": "completed"' in second_frame

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()


async def test_stream_simulation_events_reports_unknown_run(db_session: AsyncSession) -> None:
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    events = stream_simulation_events(
        session_factory, uuid.uuid4(), uuid.uuid4(), poll_interval_seconds=0.01
    )

    frame = await asyncio.wait_for(events.__anext__(), timeout=2)
    assert "event: error" in frame

    with pytest.raises(StopAsyncIteration):
        await events.__anext__()
