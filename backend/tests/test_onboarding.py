from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib import resources

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.onboarding import get_onboarding_status
from flowsage_backend.security import generate_api_key, hash_api_key
from flowsage_backend.seed import seed_baseline_personas, upsert_user
from flowsage_graph.ingest import load_events


def test_bundled_sample_data_is_complete() -> None:
    with resources.as_file(
        resources.files("flowsage_backend.resources.sample_data")
    ) as sample_dir:
        events = load_events(sample_dir / "events.jsonl")
        screenshots = sorted(p.name for p in (sample_dir / "screenshots").glob("*.png"))

    assert len(events) == 44
    assert screenshots == ["01_cart.png", "02_shipping.png", "03_confirm.png"]


async def _create_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


async def test_get_onboarding_status_all_false_for_fresh_workspace(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _create_workspace(db_session)

    status = await get_onboarding_status(db_session, workspace_id)

    assert status.has_api_key is False
    assert status.has_events is False
    assert status.has_completed_simulation is False
    assert status.has_multiple_members is False


async def test_get_onboarding_status_reflects_workspace_state(db_session: AsyncSession) -> None:
    workspace_id = await _create_workspace(db_session)

    user_a = await upsert_user(db_session, "onb-status-a@example.com", "hunter2")
    db_session.add(Membership(user_id=user_a.id, workspace_id=workspace_id, role=Role.ADMIN))
    user_b = await upsert_user(db_session, "onb-status-b@example.com", "hunter2")
    db_session.add(Membership(user_id=user_b.id, workspace_id=workspace_id, role=Role.VIEWER))

    raw_key = generate_api_key()
    db_session.add(
        ApiKey(
            workspace_id=workspace_id,
            name="k",
            key_prefix=raw_key[:12],
            key_hash=hash_api_key(raw_key),
        )
    )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id="s1",
            screen="Landing",
            event="screen_view",
            timestamp=datetime.now(timezone.utc),
        )
    )
    personas = await seed_baseline_personas(db_session, workspace_id)
    db_session.add(
        SimulationRun(
            workspace_id=workspace_id,
            flow_name="Checkout",
            goal="goal",
            persona_id=personas[0].id,
            screenshots_dir="/tmp/x",
            status=RunStatus.COMPLETED,
        )
    )
    await db_session.commit()

    status = await get_onboarding_status(db_session, workspace_id)

    assert status.has_api_key is True
    assert status.has_events is True
    assert status.has_completed_simulation is True
    assert status.has_multiple_members is True


async def test_get_onboarding_status_ignores_revoked_api_keys(db_session: AsyncSession) -> None:
    workspace_id = await _create_workspace(db_session)
    raw_key = generate_api_key()
    db_session.add(
        ApiKey(
            workspace_id=workspace_id,
            name="k",
            key_prefix=raw_key[:12],
            key_hash=hash_api_key(raw_key),
            revoked_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    status = await get_onboarding_status(db_session, workspace_id)

    assert status.has_api_key is False


from pathlib import Path

import pytest
from sqlalchemy import select

from flowsage_backend.onboarding import import_sample_data
from flowsage_backend.simulations import SimulationError


async def test_import_sample_data_ingests_events_and_creates_run(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    await seed_baseline_personas(db_session, workspace_id)
    screenshots_dir = tmp_path / "run-screens"

    result = await import_sample_data(
        db_session, workspace_id=workspace_id, screenshots_dest_dir=screenshots_dir
    )

    assert result.events_ingested == 44

    events = (
        await db_session.execute(select(Event).where(Event.workspace_id == workspace_id))
    ).scalars().all()
    assert len(events) == 44

    run = await db_session.get(SimulationRun, result.run_id)
    assert run is not None
    assert run.workspace_id == workspace_id
    assert run.flow_name == "Checkout Flow"
    assert run.goal == "Complete purchase"
    assert run.status == RunStatus.QUEUED

    assert sorted(p.name for p in screenshots_dir.iterdir()) == [
        "01_cart.png",
        "02_shipping.png",
        "03_confirm.png",
    ]


async def test_import_sample_data_uses_the_novice_baseline_persona(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    novice = next(p for p in personas if p.slug == "novice")

    result = await import_sample_data(
        db_session, workspace_id=workspace_id, screenshots_dest_dir=tmp_path / "screens"
    )

    run = await db_session.get(SimulationRun, result.run_id)
    assert run is not None
    assert run.persona_id == novice.id


async def test_import_sample_data_raises_without_seeded_personas(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    # no seed_baseline_personas call -- this workspace has no "novice" persona

    with pytest.raises(SimulationError):
        await import_sample_data(
            db_session, workspace_id=workspace_id, screenshots_dest_dir=tmp_path / "screens"
        )
