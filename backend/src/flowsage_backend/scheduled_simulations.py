"""Recurring simulation configs: create/edit, stage a fresh screenshot set via
API push, and fire due ones from worker.py's cron job. See the design spec
(docs/superpowers/specs/2026-08-01-scheduled-simulations-trend-design.md) --
this only supports the API-push trigger, not live-URL capture.

Friction-score trend reuses calibration.py's severity weighting rather than
inventing a second scoring model -- a run's score is the mean of its
per-screen predicted scores (predicted_scores_by_screen already takes the max
severity per screen; this module just averages that across screens).

Everything here is computed on demand from current data -- no persisted score
column, so it can't drift out of sync with a future severity-weight change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.calibration import predicted_scores_by_screen
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.simulations import create_run

_DUE_INTERVALS = {
    ScheduleInterval.DAILY: timedelta(days=1),
    ScheduleInterval.WEEKLY: timedelta(days=7),
}


class ScheduledSimulationError(Exception):
    """Raised when a scheduled-simulation config can't be created."""


async def create_scheduled_simulation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    persona_id: uuid.UUID,
    flow_name: str,
    goal: str,
    interval: ScheduleInterval,
    created_by: uuid.UUID | None,
) -> ScheduledSimulation:
    persona = (
        await session.execute(
            select(Persona).where(Persona.id == persona_id, Persona.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if persona is None:
        raise ScheduledSimulationError(f"No persona with id {persona_id}")

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name=flow_name,
        goal=goal,
        persona_id=persona_id,
        interval=interval,
        created_by=created_by,
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config


async def stage_screenshots(
    session: AsyncSession, config: ScheduledSimulation, screenshots_dir: Path
) -> None:
    """Records a freshly-written screenshot directory as this config's pending
    set. The caller (the push endpoint) must already have written the files to
    `screenshots_dir` before calling this -- this function only updates the
    pointer, replacing whatever pending set (if any) was there before."""
    config.pending_screenshots_dir = str(screenshots_dir)
    await session.commit()


def is_due(config: ScheduledSimulation, now: datetime) -> bool:
    if not config.active or config.pending_screenshots_dir is None:
        return False
    if config.interval == ScheduleInterval.ON_PUSH:
        return True
    if config.last_fired_at is None:
        return True
    return now - config.last_fired_at >= _DUE_INTERVALS[config.interval]


async def fire_due_scheduled_simulations(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime
) -> list[SimulationRun]:
    result = await session.execute(
        select(ScheduledSimulation).where(
            ScheduledSimulation.workspace_id == workspace_id,
            ScheduledSimulation.active.is_(True),
        )
    )
    configs = list(result.scalars().all())

    fired_runs: list[SimulationRun] = []
    for config in configs:
        if not is_due(config, now):
            continue
        # is_due already guarantees pending_screenshots_dir is not None here.
        run = await create_run(
            session,
            workspace_id=workspace_id,
            persona_id=config.persona_id,
            flow_name=config.flow_name,
            goal=config.goal,
            screenshots_dir=Path(config.pending_screenshots_dir),  # type: ignore[arg-type]
            scheduled_simulation_id=config.id,
        )
        config.last_fired_at = now
        config.pending_screenshots_dir = None
        await session.commit()
        fired_runs.append(run)
    return fired_runs


def friction_score_for_run(issues: list[FrictionIssue]) -> float:
    scores = predicted_scores_by_screen(issues)
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


class TrendPoint(BaseModel):
    run_id: uuid.UUID
    created_at: datetime
    score: float
    issue_count: int


async def build_trend(
    session: AsyncSession, workspace_id: uuid.UUID, config_id: uuid.UUID
) -> list[TrendPoint]:
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id,
            SimulationRun.scheduled_simulation_id == config_id,
            SimulationRun.status == RunStatus.COMPLETED,
        )
        .options(selectinload(SimulationRun.issues))
        .order_by(SimulationRun.created_at.asc())
    )
    runs = result.scalars().all()
    return [
        TrendPoint(
            run_id=run.id,
            created_at=run.created_at,
            score=friction_score_for_run(run.issues),
            issue_count=len(run.issues),
        )
        for run in runs
    ]


async def latest_two_completed_runs(
    session: AsyncSession, workspace_id: uuid.UUID, config_id: uuid.UUID
) -> list[SimulationRun]:
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id,
            SimulationRun.scheduled_simulation_id == config_id,
            SimulationRun.status == RunStatus.COMPLETED,
        )
        .options(selectinload(SimulationRun.issues))
        .order_by(SimulationRun.created_at.desc())
        .limit(2)
    )
    return list(result.scalars().all())
