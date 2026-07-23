"""Calibration engine: matches predicted friction (from a persona's simulation
runs) against observed friction (from the funnel/friction detector built on real
ingested events).

Matching happens per screen name, not per flow: `GET /graph/funnel` is already
global across all ingested events (there's no `flow_name` on an `Event` row), so
there's no narrower key to join predicted issues against. Only screens the
persona actually walked (i.e. has a predicted score for) are compared -- a screen
with real drop-off but no prediction isn't a calibration signal, it's just an
unsimulated screen.

Everything here is computed on demand from current data, not persisted -- no
`CalibrationRecord` table, so there's nothing to go stale or need reconciling.
"""

from __future__ import annotations

import uuid

from flowsage_graph.models import FunnelStep
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun

ANOMALY_THRESHOLD = 0.35
"""|observed - predicted| above this is flagged as a calibration anomaly, in the
same spirit as the design prototype's flagged +0.37 delta row."""

_SEVERITY_SCORES: dict[str, float] = {
    "low": 0.2,
    "medium": 0.45,
    "high": 0.7,
    "critical": 0.9,
}


def bucket_severity(severity: str) -> float:
    return _SEVERITY_SCORES.get(severity, 0.0)


def predicted_scores_by_screen(issues: list[FrictionIssue]) -> dict[str, float]:
    """Max severity score per screen -- a screen with both a low- and a
    high-severity issue reads as 'high', matching how a human skimming the
    friction report would judge that screen overall."""
    scores: dict[str, float] = {}
    for issue in issues:
        score = bucket_severity(issue.severity)
        if score > scores.get(issue.screen, 0.0):
            scores[issue.screen] = score
    return scores


class ScreenCalibration(BaseModel):
    screen: str
    predicted_score: float
    observed_score: float
    delta: float
    anomaly: bool


class PersonaCalibration(BaseModel):
    persona_id: str
    persona_name: str
    run_id: str
    screens: list[ScreenCalibration]


class AccuracyPoint(BaseModel):
    persona_id: str
    persona_name: str
    complexity: float
    accuracy: float


class CalibrationReport(BaseModel):
    personas: list[PersonaCalibration]
    accuracy_points: list[AccuracyPoint]
    has_anomaly: bool


def build_screen_calibrations(
    predicted: dict[str, float],
    funnel: list[FunnelStep],
    anomaly_threshold: float = ANOMALY_THRESHOLD,
) -> list[ScreenCalibration]:
    observed_by_screen = {step.screen: step.drop_off_rate for step in funnel}
    results = [
        ScreenCalibration(
            screen=screen,
            predicted_score=predicted_score,
            observed_score=observed_by_screen.get(screen, 0.0),
            delta=observed_by_screen.get(screen, 0.0) - predicted_score,
            anomaly=abs(observed_by_screen.get(screen, 0.0) - predicted_score) > anomaly_threshold,
        )
        for screen, predicted_score in predicted.items()
    ]
    return sorted(results, key=lambda s: s.screen)


def _complexity(screen_count: int) -> float:
    """Journey complexity proxy: how many distinct screens the persona walked,
    normalized against a fixed ceiling (10) so it plots on the same 0-1 axis as
    accuracy. A persona that only ever sees 1-2 screens isn't a complex journey;
    one that walks 10+ is treated as maximally complex."""
    return min(screen_count / 10, 1.0)


async def latest_completed_runs_by_persona(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[SimulationRun]:
    """One row per persona: their most recent COMPLETED run, if any."""
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id, SimulationRun.status == RunStatus.COMPLETED
        )
        .options(selectinload(SimulationRun.issues), selectinload(SimulationRun.persona))
        .order_by(SimulationRun.persona_id, SimulationRun.finished_at.desc())
    )
    latest_by_persona: dict[uuid.UUID, SimulationRun] = {}
    for run in result.scalars().all():
        latest_by_persona.setdefault(run.persona_id, run)
    return list(latest_by_persona.values())


async def latest_completed_run_for_persona(
    session: AsyncSession, workspace_id: uuid.UUID, persona_id: uuid.UUID
) -> SimulationRun | None:
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id,
            SimulationRun.persona_id == persona_id,
            SimulationRun.status == RunStatus.COMPLETED,
        )
        .options(selectinload(SimulationRun.issues))
        .order_by(SimulationRun.finished_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def build_calibration_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    funnel: list[FunnelStep],
    anomaly_threshold: float = ANOMALY_THRESHOLD,
) -> CalibrationReport:
    runs = await latest_completed_runs_by_persona(session, workspace_id)
    personas: list[PersonaCalibration] = []
    accuracy_points: list[AccuracyPoint] = []
    has_anomaly = False

    for run in runs:
        predicted = predicted_scores_by_screen(run.issues)
        if not predicted:
            continue

        screens = build_screen_calibrations(predicted, funnel, anomaly_threshold)
        if any(s.anomaly for s in screens):
            has_anomaly = True

        personas.append(
            PersonaCalibration(
                persona_id=str(run.persona_id),
                persona_name=run.persona.name,
                run_id=str(run.id),
                screens=screens,
            )
        )
        mean_abs_delta = sum(abs(s.delta) for s in screens) / len(screens)
        accuracy_points.append(
            AccuracyPoint(
                persona_id=str(run.persona_id),
                persona_name=run.persona.name,
                complexity=_complexity(len(screens)),
                accuracy=max(0.0, 1 - mean_abs_delta),
            )
        )

    return CalibrationReport(
        personas=personas, accuracy_points=accuracy_points, has_anomaly=has_anomaly
    )
