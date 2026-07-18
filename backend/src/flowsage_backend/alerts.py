"""Trend/alert checks reused by the dashboard banner, the weekly digest, and
(indirectly, via the same threshold definitions) the export buttons' context.
Deliberately reuses the existing calibration delta threshold
(`calibration.ANOMALY_THRESHOLD`) and a fixed churn-risk threshold rather than
introducing a configurable `AlertRule` table -- there's a single definition of
"anomalous" across the app, and no rule-config UI was scoped for this chunk.

Like `calibration.py`/`churn.py`, everything here is computed on demand from
current data -- no persisted "alert" rows.
"""

from __future__ import annotations

from flowsage_graph.funnel import discover_funnel
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.calibration import CalibrationReport, build_calibration_report
from flowsage_backend.churn import ChurnRiskSegment, build_churn_risk_segments
from flowsage_backend.events import query_events

CHURN_RISK_ALERT_THRESHOLD = 0.5
"""A churn-risk segment at or above this score is alert-worthy. Matches the
"at_risk"-vs-"healthy" fixture shape used across the existing churn tests --
comfortably above normal variance, below the churn tests' own worst-case
(~0.72 for a cohort with heavy drop-off and friction)."""


class CalibrationAlert(BaseModel):
    persona_name: str
    screen: str
    delta: float


class ChurnAlert(BaseModel):
    cohort: str
    risk_score: float
    top_reason: str


class AlertsReport(BaseModel):
    calibration_alerts: list[CalibrationAlert]
    churn_alerts: list[ChurnAlert]


def has_alerts(report: AlertsReport) -> bool:
    return bool(report.calibration_alerts or report.churn_alerts)


def check_calibration_anomalies(report: CalibrationReport) -> list[CalibrationAlert]:
    return [
        CalibrationAlert(persona_name=persona.persona_name, screen=screen.screen, delta=screen.delta)
        for persona in report.personas
        for screen in persona.screens
        if screen.anomaly
    ]


def check_churn_alerts(segments: list[ChurnRiskSegment]) -> list[ChurnAlert]:
    return [
        ChurnAlert(cohort=s.cohort, risk_score=s.risk_score, top_reason=s.top_reason)
        for s in segments
        if s.risk_score >= CHURN_RISK_ALERT_THRESHOLD
    ]


async def build_alerts_report(session: AsyncSession) -> AlertsReport:
    events = await query_events(session)
    funnel = discover_funnel(events)
    calibration_report = await build_calibration_report(session, funnel)
    churn_segments = await build_churn_risk_segments(session)
    return AlertsReport(
        calibration_alerts=check_calibration_anomalies(calibration_report),
        churn_alerts=check_churn_alerts(churn_segments),
    )


def build_digest_text(report: AlertsReport) -> str:
    """Plain-text fallback for Slack's top-level `text` field (used in
    notification previews; `build_digest_blocks` is the rendered body)."""
    if not has_alerts(report):
        return "FlowSage Weekly Digest: no calibration or churn alerts this week."
    parts = [
        f"{len(report.calibration_alerts)} calibration anomalies",
        f"{len(report.churn_alerts)} churn-risk segments",
    ]
    return "FlowSage Weekly Digest: " + ", ".join(parts)


def build_digest_blocks(report: AlertsReport) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "FlowSage Weekly Digest"}},
    ]
    if not has_alerts(report):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "No calibration or churn alerts this week."},
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
    return blocks
