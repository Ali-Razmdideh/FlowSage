"""Recurring simulation config endpoints: create/list/edit/delete a schedule,
push a fresh screenshot set for it to consume on its next due check, and read
its friction-score trend. Firing itself happens in worker.py's cron job, not
here -- see flowsage_backend.scheduled_simulations.fire_due_scheduled_simulations.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import record_audit_event
from flowsage_backend.deps import get_current_actor, get_db_session
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.scheduled_simulations import (
    ScheduledSimulationError,
    TrendPoint,
    build_trend,
    create_scheduled_simulation,
    stage_screenshots,
)
from flowsage_backend.simulations import IMAGE_SUFFIXES

router = APIRouter(prefix="/scheduled-simulations", tags=["scheduled-simulations"])


class ScheduledSimulationOut(BaseModel):
    id: uuid.UUID
    flow_name: str
    goal: str
    persona_id: uuid.UUID
    interval: ScheduleInterval
    active: bool
    has_pending_screenshots: bool
    last_fired_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, config: ScheduledSimulation) -> "ScheduledSimulationOut":
        return cls(
            id=config.id,
            flow_name=config.flow_name,
            goal=config.goal,
            persona_id=config.persona_id,
            interval=config.interval,
            active=config.active,
            has_pending_screenshots=config.pending_screenshots_dir is not None,
            last_fired_at=config.last_fired_at,
            created_at=config.created_at,
        )


class ScheduledSimulationCreate(BaseModel):
    persona_id: uuid.UUID
    flow_name: str
    goal: str
    interval: ScheduleInterval


class ScheduledSimulationUpdate(BaseModel):
    goal: str | None = None
    interval: ScheduleInterval | None = None
    active: bool | None = None


async def _get_config(
    session: AsyncSession, workspace_id: uuid.UUID, config_id: uuid.UUID
) -> ScheduledSimulation:
    result = await session.execute(
        select(ScheduledSimulation).where(
            ScheduledSimulation.id == config_id, ScheduledSimulation.workspace_id == workspace_id
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled simulation not found")
    return config


@router.post("", response_model=ScheduledSimulationOut, status_code=status.HTTP_201_CREATED)
async def create_scheduled_simulation_endpoint(
    payload: ScheduledSimulationCreate,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduledSimulationOut:
    workspace_id, user_id = actor
    try:
        config = await create_scheduled_simulation(
            session,
            workspace_id=workspace_id,
            persona_id=payload.persona_id,
            flow_name=payload.flow_name,
            goal=payload.goal,
            interval=payload.interval,
            created_by=user_id,
        )
    except ScheduledSimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await record_audit_event(
        session,
        workspace_id,
        actor_user_id=user_id,
        action="scheduled_simulation.created",
        target_type="scheduled_simulation",
        target_id=str(config.id),
        extra_data={"flow_name": payload.flow_name, "interval": payload.interval.value},
    )
    return ScheduledSimulationOut.from_model(config)


@router.get("", response_model=list[ScheduledSimulationOut])
async def list_scheduled_simulations(
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> list[ScheduledSimulationOut]:
    workspace_id, _ = actor
    result = await session.execute(
        select(ScheduledSimulation).where(ScheduledSimulation.workspace_id == workspace_id)
    )
    return [ScheduledSimulationOut.from_model(c) for c in result.scalars().all()]


@router.patch("/{config_id}", response_model=ScheduledSimulationOut)
async def update_scheduled_simulation(
    config_id: uuid.UUID,
    payload: ScheduledSimulationUpdate,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduledSimulationOut:
    workspace_id, _ = actor
    config = await _get_config(session, workspace_id, config_id)
    if payload.goal is not None:
        config.goal = payload.goal
    if payload.interval is not None:
        config.interval = payload.interval
    if payload.active is not None:
        config.active = payload.active
    await session.commit()
    await session.refresh(config)
    return ScheduledSimulationOut.from_model(config)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_scheduled_simulation(
    config_id: uuid.UUID,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    workspace_id, _ = actor
    config = await _get_config(session, workspace_id, config_id)
    await session.delete(config)
    await session.commit()


@router.post("/{config_id}/screenshots", response_model=ScheduledSimulationOut)
async def push_screenshots(
    request: Request,
    config_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduledSimulationOut:
    workspace_id, _ = actor
    config = await _get_config(session, workspace_id, config_id)
    settings = request.app.state.settings
    previous_pending_dir = (
        Path(config.pending_screenshots_dir) if config.pending_screenshots_dir else None
    )
    # Each push gets its own directory (never reused across pushes) so a later
    # push can never corrupt an earlier one's already-staged or already-fired
    # files -- a fired run keeps reading its own screenshots_dir forever,
    # untouched by any subsequent push to this config.
    new_dir = Path(settings.upload_dir) / "scheduled" / str(config.id) / uuid.uuid4().hex
    new_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        # .name strips directory components -- see the identical guard in
        # api/simulations.py's create_simulation for why this matters.
        filename = Path(upload.filename or "").name
        if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
            shutil.rmtree(new_dir, ignore_errors=True)
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unsupported file type: {filename!r}"
            )
        (new_dir / filename).write_bytes(await upload.read())

    await stage_screenshots(session, config, new_dir)
    # The previous pending set (if any) was never consumed by a fired run --
    # once a run fires it clears pending_screenshots_dir, so if it's still set
    # here it's safe to discard now that the new set has replaced it.
    if previous_pending_dir is not None:
        shutil.rmtree(previous_pending_dir, ignore_errors=True)
    return ScheduledSimulationOut.from_model(config)


@router.get("/{config_id}/trend", response_model=list[TrendPoint])
async def get_trend(
    config_id: uuid.UUID,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> list[TrendPoint]:
    workspace_id, _ = actor
    await _get_config(session, workspace_id, config_id)  # 404s if missing/wrong workspace
    return await build_trend(session, workspace_id, config_id)
