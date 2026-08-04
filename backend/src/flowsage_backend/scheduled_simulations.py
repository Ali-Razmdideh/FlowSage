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

import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.billing import get_usage
from flowsage_backend.calibration import predicted_scores_by_screen
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.simulations import create_run

logger = logging.getLogger(__name__)

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
        # SKIP LOCKED, not a blocking FOR UPDATE: a config an in-flight push
        # (see push_screenshots' own row lock) is mid-write on just gets
        # skipped this pass and picked up on the next hourly cron tick,
        # rather than the whole batch stalling on one contended row. Without
        # this lock at all, a push racing this read-then-clear could still
        # read this run's about-to-be-cleared pending_screenshots_dir as its
        # "previous" and rmtree the directory this run just started reading.
        .with_for_update(skip_locked=True)
    )
    configs = list(result.scalars().all())

    usage = await get_usage(session, workspace_id)
    remaining_budget = None if usage.runs_limit == -1 else usage.runs_limit - usage.runs_used

    # Snapshot every attribute this loop needs per config *before* the loop
    # starts firing anything. A commit or rollback mid-loop (both happen
    # below) expires every object in the session's identity map -- that's
    # SQLAlchemy's default post-transaction behavior, not specific to the
    # failure path. Re-reading an expired attribute afterwards via ordinary
    # (un-awaited) attribute access raises MissingGreenlet, since a lazy
    # reload needs IO that only an explicit `await session.refresh(...)`
    # can provide. Capturing plain values up front means the loop body only
    # ever *writes* to a `config` object (never reads one back), and plain
    # attribute writes don't require a reload regardless of expiry.
    due = [
        (
            config,
            config.id,
            config.persona_id,
            config.flow_name,
            config.goal,
            config.pending_screenshots_dir,
        )
        for config in configs
        if is_due(config, now)
    ]

    fired_runs: list[SimulationRun] = []
    for config, config_id, persona_id, flow_name, goal, pending_dir in due:
        if remaining_budget is not None and remaining_budget <= 0:
            # Tier cap reached -- leave this config's pending set staged and
            # last_fired_at untouched so it's picked back up automatically
            # once the workspace's usage resets or upgrades, instead of
            # silently dropping the pending screenshots.
            continue
        try:
            run = await create_run(
                session,
                workspace_id=workspace_id,
                persona_id=persona_id,
                flow_name=flow_name,
                goal=goal,
                screenshots_dir=Path(pending_dir),  # type: ignore[arg-type]
                scheduled_simulation_id=config_id,
            )
            config.last_fired_at = now
            config.pending_screenshots_dir = None
            await session.commit()
        except Exception:
            await session.rollback()
            logger.warning(
                "Failed to fire scheduled simulation %s in workspace %s",
                config_id,
                workspace_id,
                exc_info=True,
            )
            continue
        fired_runs.append(run)
        if remaining_budget is not None:
            remaining_budget -= 1
    return fired_runs


def friction_score_for_run(issues: list[FrictionIssue], screens_walked: int) -> float:
    if screens_walked <= 0:
        return 0.0
    scores = predicted_scores_by_screen(issues)
    return sum(scores.values()) / screens_walked


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
        .options(selectinload(SimulationRun.issues), selectinload(SimulationRun.steps))
        .order_by(SimulationRun.created_at.asc())
    )
    runs = result.scalars().all()
    return [
        TrendPoint(
            run_id=run.id,
            created_at=run.created_at,
            score=friction_score_for_run(run.issues, len({step.screen for step in run.steps})),
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
        .options(selectinload(SimulationRun.issues), selectinload(SimulationRun.steps))
        .order_by(SimulationRun.created_at.desc())
        .limit(2)
    )
    return list(result.scalars().all())
