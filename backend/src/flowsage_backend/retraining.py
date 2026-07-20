"""Retraining job lifecycle: heuristic slider nudge based on observed-vs-predicted
friction, no LLM call involved (deterministic, unit-testable, matches the plan's
"slider re-fit" language without adding per-retraining Claude cost/latency).

For each anomalous screen (see `flowsage_backend.calibration`), nudge sliders in
the direction the evidence points: if real users hit *more* friction than
predicted, the persona was modeled as more capable/patient than reality, so
`anxiety` rises while `patience`/`technical_literacy` fall, proportional to the
delta magnitude -- and the reverse when the persona over-predicted friction that
didn't materialize. `curiosity` is left alone; nothing in the observed signal
(drop-off rate) speaks to a persona's curiosity.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flowsage_graph.funnel import discover_funnel

from flowsage_backend.calibration import (
    ScreenCalibration,
    build_screen_calibrations,
    latest_completed_run_for_persona,
    predicted_scores_by_screen,
)
from flowsage_backend.events import query_events
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.persona import Persona, PersonaMemory
from flowsage_backend.settings_store import get_or_create_calibration_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NUDGE_STEP = 0.05


class RetrainingError(Exception):
    """Raised when a retraining job can't be created or found."""


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def nudge_sliders(
    persona: Persona, anomalies: list[ScreenCalibration]
) -> tuple[float, float, float]:
    """Returns the persona's new (technical_literacy, anxiety, patience)."""
    technical_literacy = persona.technical_literacy
    anxiety = persona.anxiety
    patience = persona.patience

    for anomaly in anomalies:
        step = NUDGE_STEP * min(abs(anomaly.delta), 1.0)
        if anomaly.delta > 0:
            # Real users hit more friction here than predicted.
            anxiety += step
            patience -= step
            technical_literacy -= step
        else:
            # Real users hit less friction here than predicted.
            anxiety -= step
            patience += step
            technical_literacy += step

    return _clamp01(technical_literacy), _clamp01(anxiety), _clamp01(patience)


async def create_retraining_job(
    session: AsyncSession, persona_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> RetrainingJob:
    persona = (
        await session.execute(
            select(Persona).where(Persona.id == persona_id, Persona.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if persona is None:
        raise RetrainingError(f"No persona with id {persona_id}")

    job = RetrainingJob(
        workspace_id=workspace_id, persona_id=persona_id, status=RetrainingStatus.QUEUED
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def execute_retraining(session: AsyncSession, job_id: uuid.UUID) -> None:
    job = await session.get(RetrainingJob, job_id)
    if job is None:
        raise RetrainingError(f"No retraining job with id {job_id}")

    job.status = RetrainingStatus.RUNNING
    job.started_at = datetime.now(timezone.utc)
    await session.commit()

    try:
        persona = await session.get(Persona, job.persona_id)
        if persona is None:
            raise RetrainingError(f"No persona with id {job.persona_id}")

        latest_run = await latest_completed_run_for_persona(session, job.workspace_id, persona.id)
        predicted = predicted_scores_by_screen(latest_run.issues) if latest_run else {}

        events = await query_events(session, job.workspace_id)
        funnel = discover_funnel(events)
        settings = await get_or_create_calibration_settings(session, job.workspace_id)
        screens = build_screen_calibrations(predicted, funnel, settings.anomaly_threshold)
        anomalies = [s for s in screens if s.anomaly]

        job.total_epochs = max(len(anomalies), 1)
        await session.commit()

        for i in range(1, len(anomalies) + 1):
            job.epoch = i
            job.progress = i / job.total_epochs * 100
            await session.commit()

        if anomalies:
            new_literacy, new_anxiety, new_patience = nudge_sliders(persona, anomalies)
            persona.technical_literacy = new_literacy
            persona.anxiety = new_anxiety
            persona.patience = new_patience

            screens_summary = ", ".join(f"{a.screen} (delta {a.delta:+.2f})" for a in anomalies)
            note = (
                f"Adjusted sliders after {len(anomalies)} anomalous screen(s): "
                f"{screens_summary}. New sliders -- technical_literacy={new_literacy:.2f}, "
                f"anxiety={new_anxiety:.2f}, patience={new_patience:.2f}."
            )
        else:
            note = "No screens exceeded the calibration anomaly threshold; sliders unchanged."

        session.add(
            PersonaMemory(
                workspace_id=job.workspace_id,
                persona_id=persona.id,
                title=(
                    "Retrained from observed behavioral evidence"
                    if anomalies
                    else "Retraining skipped -- no anomalies"
                ),
                note=note,
                kind="retraining",
            )
        )

        job.epoch = job.total_epochs
        job.progress = 100.0
        job.status = RetrainingStatus.COMPLETED
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception as exc:
        job.status = RetrainingStatus.FAILED
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        raise
