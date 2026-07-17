"""Simulation run lifecycle: create a run, then execute it.

Execution walks the run's screenshots with its persona using
`flowsage_predict.agent.iter_persona_walkthrough`, persisting each step (and any
friction issue) as soon as it happens, so a caller polling/streaming the run sees
live progress -- matching the "Running Simulation" prototype's live agentic log.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from flowsage_predict.agent import AgentState, iter_persona_walkthrough
from flowsage_predict.vision import VisionClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import (
    FrictionIssue,
    RunStatus,
    SimulationRun,
    SimulationStep,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class SimulationError(Exception):
    """Raised when a run can't be created or found."""


def discover_screenshots(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


async def create_run(
    session: AsyncSession,
    *,
    persona_id: uuid.UUID,
    flow_name: str,
    goal: str,
    screenshots_dir: Path,
    run_id: uuid.UUID | None = None,
) -> SimulationRun:
    """`run_id` lets a caller that already picked a directory name (e.g. the upload
    endpoint, which needs an id before it can save files) reuse it as the row's id."""
    persona = await session.get(Persona, persona_id)
    if persona is None:
        raise SimulationError(f"No persona with id {persona_id}")

    if not discover_screenshots(screenshots_dir):
        raise SimulationError(f"No screenshots found in {screenshots_dir}")

    run = SimulationRun(
        id=run_id if run_id is not None else uuid.uuid4(),
        flow_name=flow_name,
        goal=goal,
        persona_id=persona.id,
        screenshots_dir=str(screenshots_dir),
        status=RunStatus.QUEUED,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


def _next_state(iterator: Iterator[AgentState]) -> AgentState | None:
    """Advance a sync generator by one step. Runs inside a thread (see below) so the
    blocking vision-client call inside it doesn't stall the asyncio event loop."""
    try:
        return next(iterator)
    except StopIteration:
        return None


async def execute_simulation(
    session: AsyncSession,
    run_id: uuid.UUID,
    vision_client: VisionClient,
) -> None:
    result = await session.execute(
        select(SimulationRun)
        .where(SimulationRun.id == run_id)
        .options(selectinload(SimulationRun.persona))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise SimulationError(f"No simulation run with id {run_id}")

    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(timezone.utc)
    await session.commit()

    screenshots = discover_screenshots(Path(run.screenshots_dir))
    persona = run.persona.to_predict_persona()

    try:
        iterator = iter_persona_walkthrough(
            persona=persona, goal=run.goal, screenshots=screenshots, vision_client=vision_client
        )
        persisted_step_count = 0
        while True:
            state = await asyncio.to_thread(_next_state, iterator)
            if state is None:
                break

            for offset, predict_step in enumerate(state["steps"][persisted_step_count:]):
                step_row = SimulationStep(
                    run_id=run.id,
                    sequence=persisted_step_count + offset,
                    screen=predict_step.screen,
                    action=predict_step.action,
                    reasoning=predict_step.reasoning,
                )
                session.add(step_row)
                await session.flush()  # populate step_row.id for the FrictionIssue FK

                if predict_step.friction is not None:
                    issue = predict_step.friction
                    session.add(
                        FrictionIssue(
                            run_id=run.id,
                            step_id=step_row.id,
                            screen=issue.screen,
                            severity=issue.severity.value,
                            title=issue.title,
                            heuristic_violated=issue.heuristic_violated,
                            persona_impact=issue.persona_impact,
                            description=issue.description,
                            suggested_fix=issue.suggested_fix,
                        )
                    )
            persisted_step_count = len(state["steps"])
            await session.commit()

        run.status = RunStatus.COMPLETED
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        raise
