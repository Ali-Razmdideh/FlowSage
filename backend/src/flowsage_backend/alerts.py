"""Trend/alert checks reused by the dashboard banner, the digest job, and
(indirectly, via the same threshold definitions) the export buttons' context.
Reuses the calibration delta threshold and a churn-risk threshold -- there's a
single definition of "anomalous" across the app, not a per-rule config table --
but both thresholds are now editable via `/settings/model-calibration`
(`flowsage_backend.models.settings.CalibrationSettings`), not hardcoded; the
module constants below remain as the defaults a fresh settings row is seeded
with.

Like `calibration.py`/`churn.py`, everything here is computed on demand from
current data -- no persisted "alert" rows.
"""

from __future__ import annotations

import uuid

from flowsage_graph.funnel import discover_funnel
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.calibration import CalibrationReport, build_calibration_report
from flowsage_backend.churn import ChurnRiskSegment, build_churn_risk_segments
from flowsage_backend.events import query_events
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation
from flowsage_backend.scheduled_simulations import friction_score_for_run, latest_two_completed_runs
from flowsage_backend.settings_store import get_or_create_calibration_settings

CHURN_RISK_ALERT_THRESHOLD = 0.5
"""A churn-risk segment at or above this score is alert-worthy. Matches the
"at_risk"-vs-"healthy" fixture shape used across the existing churn tests --
comfortably above normal variance, below the churn tests' own worst-case
(~0.72 for a cohort with heavy drop-off and friction)."""

FRICTION_REGRESSION_ALERT_THRESHOLD = 0.15
"""A scheduled config's latest fired run scoring this much higher than its
previous fired run is alert-worthy. Tighter than calibration.py's
ANOMALY_THRESHOLD (0.35) because this compares a metric against its own
recent history, not against an independent observed signal -- a smaller
jump is already meaningful there."""


class CalibrationAlert(BaseModel):
    persona_name: str
    screen: str
    delta: float


class ChurnAlert(BaseModel):
    cohort: str
    risk_score: float
    top_reason: str


class FrictionRegressionAlert(BaseModel):
    scheduled_simulation_id: uuid.UUID
    flow_name: str
    previous_score: float
    current_score: float
    delta: float


class AlertsReport(BaseModel):
    calibration_alerts: list[CalibrationAlert]
    churn_alerts: list[ChurnAlert]
    friction_regression_alerts: list[FrictionRegressionAlert]


def has_alerts(report: AlertsReport) -> bool:
    return bool(
        report.calibration_alerts or report.churn_alerts or report.friction_regression_alerts
    )


def check_calibration_anomalies(report: CalibrationReport) -> list[CalibrationAlert]:
    return [
        CalibrationAlert(
            persona_name=persona.persona_name, screen=screen.screen, delta=screen.delta
        )
        for persona in report.personas
        for screen in persona.screens
        if screen.anomaly
    ]


def check_churn_alerts(
    segments: list[ChurnRiskSegment], churn_risk_alert_threshold: float = CHURN_RISK_ALERT_THRESHOLD
) -> list[ChurnAlert]:
    return [
        ChurnAlert(cohort=s.cohort, risk_score=s.risk_score, top_reason=s.top_reason)
        for s in segments
        if s.risk_score >= churn_risk_alert_threshold
    ]


async def check_friction_regression_alerts(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[FrictionRegressionAlert]:
    configs = (
        (
            await session.execute(
                select(ScheduledSimulation).where(
                    ScheduledSimulation.workspace_id == workspace_id,
                    ScheduledSimulation.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    alerts: list[FrictionRegressionAlert] = []
    for config in configs:
        runs = await latest_two_completed_runs(session, workspace_id, config.id)
        if len(runs) < 2:
            continue
        current, previous = runs[0], runs[1]
        current_score = friction_score_for_run(
            current.issues, len({step.screen for step in current.steps})
        )
        previous_score = friction_score_for_run(
            previous.issues, len({step.screen for step in previous.steps})
        )
        delta = current_score - previous_score
        if delta >= FRICTION_REGRESSION_ALERT_THRESHOLD:
            alerts.append(
                FrictionRegressionAlert(
                    scheduled_simulation_id=config.id,
                    flow_name=config.flow_name,
                    previous_score=previous_score,
                    current_score=current_score,
                    delta=delta,
                )
            )
    return alerts


async def build_alerts_report(session: AsyncSession, workspace_id: uuid.UUID) -> AlertsReport:
    events = await query_events(session, workspace_id)
    funnel = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, workspace_id)
    calibration_report = await build_calibration_report(
        session, workspace_id, funnel, settings.anomaly_threshold
    )
    churn_segments = await build_churn_risk_segments(session, workspace_id)
    return AlertsReport(
        calibration_alerts=check_calibration_anomalies(calibration_report),
        churn_alerts=check_churn_alerts(churn_segments, settings.churn_risk_alert_threshold),
        friction_regression_alerts=await check_friction_regression_alerts(session, workspace_id),
    )


def build_digest_text(report: AlertsReport) -> str:
    """Plain-text fallback for Slack's top-level `text` field (used in
    notification previews; `build_digest_blocks` is the rendered body)."""
    if not has_alerts(report):
        return "FlowSage Digest: no calibration, churn, or friction-regression alerts."
    parts = [
        f"{len(report.calibration_alerts)} calibration anomalies",
        f"{len(report.churn_alerts)} churn-risk segments",
        f"{len(report.friction_regression_alerts)} friction regressions",
    ]
    return "FlowSage Digest: " + ", ".join(parts)


def build_digest_blocks(report: AlertsReport) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "FlowSage Digest"}},
    ]
    if not has_alerts(report):
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No calibration, churn, or friction-regression alerts.",
                },
            }
        )
        return blocks

    for cal_alert in report.calibration_alerts:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Calibration anomaly*: {cal_alert.persona_name} on `{cal_alert.screen}` "
                        f"(delta {cal_alert.delta:+.2f})"
                    ),
                },
            }
        )
    for churn_alert in report.churn_alerts:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Churn risk*: {churn_alert.cohort} at {churn_alert.risk_score * 100:.0f}% "
                        f"-- {churn_alert.top_reason}"
                    ),
                },
            }
        )
    for regression in report.friction_regression_alerts:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Friction regression*: {regression.flow_name} jumped to "
                        f"{regression.current_score:.2f} (+{regression.delta:.2f})"
                    ),
                },
            }
        )
    return blocks
