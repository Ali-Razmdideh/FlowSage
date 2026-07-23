"""Calibration Insights endpoints: predicted-vs-observed friction report, and
async retraining jobs (arq + SSE, same pattern as `api/simulations.py`)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from flowsage_graph.funnel import discover_funnel
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowsage_backend.calibration import CalibrationReport, build_calibration_report
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.events import query_events
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership
from flowsage_backend.retraining import RetrainingError, create_retraining_job
from flowsage_backend.settings_store import get_or_create_calibration_settings

router = APIRouter(
    prefix="/calibration", tags=["calibration"], dependencies=[Depends(get_current_membership)]
)


class RetrainRequest(BaseModel):
    persona_id: uuid.UUID


class RetrainingJobOut(BaseModel):
    id: uuid.UUID
    persona_id: uuid.UUID
    status: RetrainingStatus
    epoch: int
    total_epochs: int
    progress: float
    error: str | None

    @classmethod
    def from_row(cls, job: RetrainingJob) -> "RetrainingJobOut":
        return cls(
            id=job.id,
            persona_id=job.persona_id,
            status=job.status,
            epoch=job.epoch,
            total_epochs=job.total_epochs,
            progress=job.progress,
            error=job.error,
        )


@router.get("/report", response_model=CalibrationReport)
async def get_calibration_report(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CalibrationReport:
    _, membership = membership_pair
    events = await query_events(session, membership.workspace_id)
    funnel = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, membership.workspace_id)
    return await build_calibration_report(
        session, membership.workspace_id, funnel, settings.anomaly_threshold
    )


@router.post("/retrain", response_model=RetrainingJobOut, status_code=status.HTTP_201_CREATED)
async def start_retraining(
    payload: RetrainRequest,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> RetrainingJobOut:
    _, membership = membership_pair
    try:
        job = await create_retraining_job(
            session, payload.persona_id, workspace_id=membership.workspace_id
        )
    except RetrainingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await request.app.state.arq_pool.enqueue_job("run_retraining_job", str(job.id))
    return RetrainingJobOut.from_row(job)


@router.get("/retrain/{job_id}", response_model=RetrainingJobOut)
async def get_retraining_job(
    job_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> RetrainingJobOut:
    _, membership = membership_pair
    job = await session.get(RetrainingJob, job_id)
    if job is None or job.workspace_id != membership.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Retraining job not found")
    return RetrainingJobOut.from_row(job)


def _sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_retraining_events(
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    poll_interval_seconds: float = 0.5,
) -> AsyncIterator[str]:
    """DB-polling SSE loop, same reasoning as `stream_simulation_events`: no extra
    infra beyond the Postgres this single-tenant app already has."""
    while True:
        async with session_factory() as session:
            job = await session.get(RetrainingJob, job_id)

        if job is None or job.workspace_id != workspace_id:
            yield _sse_event("error", {"detail": "Retraining job not found"})
            return

        yield _sse_event("progress", RetrainingJobOut.from_row(job).model_dump(mode="json"))

        if job.status in (RetrainingStatus.COMPLETED, RetrainingStatus.FAILED):
            yield _sse_event("done", {"status": job.status.value, "error": job.error})
            return

        await asyncio.sleep(poll_interval_seconds)


@router.get("/retrain/{job_id}/stream")
async def stream_retraining(
    job_id: uuid.UUID,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
) -> StreamingResponse:
    _, membership = membership_pair
    session_factory = request.app.state.session_factory
    return StreamingResponse(
        stream_retraining_events(session_factory, membership.workspace_id, job_id),
        media_type="text/event-stream",
    )
