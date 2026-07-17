"""Simulation run endpoints: upload a screenshot sequence, watch it run, read results.

`POST /simulations` saves the uploaded screenshots and enqueues a `run_simulation_job`
on the arq/Redis queue (see `worker.py`) rather than running the walkthrough inline --
Claude vision calls per screen make this too slow for a single request/response cycle.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from flowsage_backend.deps import get_current_user, get_db_session
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.simulations import IMAGE_SUFFIXES, SimulationError, create_run

router = APIRouter(
    prefix="/simulations", tags=["simulations"], dependencies=[Depends(get_current_user)]
)


class FrictionIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    screen: str
    severity: str
    title: str
    heuristic_violated: str
    persona_impact: str
    description: str
    suggested_fix: str


class SimulationStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    screen: str
    action: str
    reasoning: str


class SimulationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    flow_name: str
    goal: str
    persona_id: uuid.UUID
    status: RunStatus
    error: str | None


class SimulationRunDetailOut(SimulationRunOut):
    steps: list[SimulationStepOut]
    issues: list[FrictionIssueOut]


async def _load_run_with_children(session: AsyncSession, run_id: uuid.UUID) -> SimulationRun | None:
    result = await session.execute(
        select(SimulationRun)
        .where(SimulationRun.id == run_id)
        .options(selectinload(SimulationRun.steps), selectinload(SimulationRun.issues))
    )
    return result.scalar_one_or_none()


@router.post("", response_model=SimulationRunOut, status_code=status.HTTP_201_CREATED)
async def create_simulation(
    request: Request,
    persona_id: uuid.UUID = Form(...),
    goal: str = Form(...),
    flow_name: str = Form(...),
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> SimulationRun:
    settings = request.app.state.settings
    run_id = uuid.uuid4()
    screenshots_dir = Path(settings.upload_dir) / str(run_id)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        # .name strips any directory components from the client-supplied filename,
        # so a crafted "../../etc/passwd"-style name can't escape screenshots_dir.
        filename = Path(upload.filename or "").name
        if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unsupported file type: {filename!r}"
            )
        (screenshots_dir / filename).write_bytes(await upload.read())

    try:
        run = await create_run(
            session,
            run_id=run_id,
            persona_id=persona_id,
            flow_name=flow_name,
            goal=goal,
            screenshots_dir=screenshots_dir,
        )
    except SimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await request.app.state.arq_pool.enqueue_job("run_simulation_job", str(run.id))
    return run


@router.get("/{run_id}", response_model=SimulationRunDetailOut)
async def get_simulation(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> SimulationRun:
    run = await _load_run_with_children(session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation run not found")
    return run


def _sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_simulation_events(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    *,
    poll_interval_seconds: float = 0.5,
) -> AsyncIterator[str]:
    """Poll the DB for new steps/status until the run finishes, yielding SSE frames.

    A DB-polling loop, not Redis pub/sub, on purpose: it's simpler and needs no extra
    infrastructure beyond Postgres, which this single-tenant Phase 1 already has.
    """
    sent_step_count = 0
    while True:
        async with session_factory() as session:
            run = await _load_run_with_children(session, run_id)

        if run is None:
            yield _sse_event("error", {"detail": "Simulation run not found"})
            return

        for step in run.steps[sent_step_count:]:
            yield _sse_event("step", SimulationStepOut.model_validate(step).model_dump(mode="json"))
        sent_step_count = len(run.steps)

        if run.status in (RunStatus.COMPLETED, RunStatus.FAILED):
            yield _sse_event("done", {"status": run.status.value, "error": run.error})
            return

        await asyncio.sleep(poll_interval_seconds)


@router.get("/{run_id}/stream")
async def stream_simulation(run_id: uuid.UUID, request: Request) -> StreamingResponse:
    session_factory = request.app.state.session_factory
    return StreamingResponse(
        stream_simulation_events(session_factory, run_id), media_type="text/event-stream"
    )
