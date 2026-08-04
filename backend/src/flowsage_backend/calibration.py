"""Calibration engine: matches predicted friction (from a persona's simulation
runs) against observed friction (from the funnel/friction detector built on real
ingested events).

Matching happens per screen name, not per flow: `GET /graph/funnel` is already
global across all ingested events (there's no `flow_name` on an `Event` row), so
there's no narrower key to join predicted issues against. Only screens the
persona actually walked (i.e. has a predicted score for) are compared -- a screen
with real drop-off but no prediction isn't a calibration signal, it's just an
unsimulated screen.

The calibration matching/anomaly detection itself is computed on demand from
current data, not persisted -- no `CalibrationRecord` table, so there's
nothing to go stale or need reconciling there.

`PersonaCalibration.narrative` is the exception: it's a real, cached
Claude-generated explanation of *why* an anomalous persona/screen pair
diverges, looked up from `GeneratedInsight` (see `insight_cache.py`) by an
input hash of the anomalous screens. A cache miss leaves `narrative` as
`None` (the API layer enqueues a background job to fill it in for next time,
see `api/calibration.py`) -- input-hash staleness detection is exactly the
mechanism that decides whether the persisted narrative still matches the
current anomaly signal or needs regenerating.
"""

from __future__ import annotations

import logging
import uuid

from flowsage_graph.funnel import discover_funnel
from flowsage_graph.models import FunnelStep
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend import insight_cache
from flowsage_backend.events import query_events
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.settings_store import get_or_create_calibration_settings
from flowsage_predict.narrative import NARRATIVE_MODEL, NarrativeClient, ScreenSignal

logger = logging.getLogger(__name__)

CALIBRATION_NARRATIVE_KIND = "calibration_anomaly"

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
    narrative: str | None = None


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


def calibration_input_hash(anomalies: list[ScreenCalibration]) -> str:
    signal: dict[str, object] = {
        "screens": sorted(
            (
                {
                    "screen": a.screen,
                    "predicted_score": round(a.predicted_score, 4),
                    "observed_score": round(a.observed_score, 4),
                }
                for a in anomalies
            ),
            key=lambda d: str(d["screen"]),
        )
    }
    return insight_cache.compute_input_hash(signal)


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
        anomalies = [s for s in screens if s.anomaly]
        if anomalies:
            has_anomaly = True

        narrative: str | None = None
        if anomalies:
            input_hash = calibration_input_hash(anomalies)
            cached = await insight_cache.get_cached(
                session, workspace_id, CALIBRATION_NARRATIVE_KIND, str(run.persona_id)
            )
            if cached is not None and cached.input_hash == input_hash:
                narrative = str(cached.payload["narrative"])

        personas.append(
            PersonaCalibration(
                persona_id=str(run.persona_id),
                persona_name=run.persona.name,
                run_id=str(run.id),
                screens=screens,
                narrative=narrative,
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


async def generate_and_cache_calibration_narrative(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    persona_id: uuid.UUID,
    narrative_client: NarrativeClient,
) -> None:
    """Runs inside the arq worker (`generate_calibration_narrative_job`) --
    re-derives the persona's current anomalous screens fresh, same reasoning
    as `churn.generate_and_cache_node_insight`."""
    persona = await session.get(Persona, persona_id)
    run = await latest_completed_run_for_persona(session, workspace_id, persona_id)
    if persona is None or run is None:
        return

    predicted = predicted_scores_by_screen(run.issues)
    if not predicted:
        return

    events = await query_events(session, workspace_id)
    funnel: list[FunnelStep] = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, workspace_id)
    screens = build_screen_calibrations(predicted, funnel, settings.anomaly_threshold)
    anomalies = [s for s in screens if s.anomaly]
    if not anomalies:
        return

    try:
        narrative = narrative_client.generate_calibration_narrative(
            persona.name,
            [
                ScreenSignal(
                    screen=a.screen,
                    predicted_score=a.predicted_score,
                    observed_score=a.observed_score,
                    delta=a.delta,
                )
                for a in anomalies
            ],
        )
    except Exception:  # noqa: BLE001 - see generate_and_cache_node_insight
        logger.warning(
            "Calibration narrative generation failed for persona %s", persona_id, exc_info=True
        )
        return

    input_hash = calibration_input_hash(anomalies)
    await insight_cache.upsert_cached(
        session,
        workspace_id,
        CALIBRATION_NARRATIVE_KIND,
        str(persona_id),
        input_hash,
        {"narrative": narrative},
        NARRATIVE_MODEL,
    )
