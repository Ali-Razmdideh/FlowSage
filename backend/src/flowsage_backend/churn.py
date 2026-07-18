"""Cohort path comparison, per-segment churn-risk scoring, and per-node
re-engagement recommendations -- all built on top of the same
`discover_funnel`/`detect_friction` primitives `build_funnel_report` already
uses (see `events.py`).

Like `calibration.py`, everything here is computed on demand from current
events -- no new tables. Churn-risk scoring and the "AI Insight"/recommendation
text are deterministic heuristics, not an LLM call: matching the philosophy
behind `retraining.py`'s slider nudge, a live Claude call for every funnel
screen on every page load would add cost/latency the plan doesn't ask for, and
a heuristic keeps this endpoint fast and side-effect free.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from flowsage_graph.funnel import detect_friction, discover_funnel
from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FrictionKind, FrictionNode, FunnelReport, FunnelStep
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.events import build_funnel_report, distinct_cohorts, query_events

CHURN_DROP_OFF_WEIGHT = 0.6
CHURN_FRICTION_WEIGHT = 0.4
"""Churn-risk score = weighted blend of mean funnel drop-off and friction
density. Weighted toward drop-off since it's the more direct abandonment
signal; friction density is a secondary corroborating signal."""

MAX_RECOMMENDATIONS = 3
"""Matches the design prototype's numbered 1-2-3 recommendation list."""


class CohortFunnelSummary(BaseModel):
    cohort: str
    total_sessions: int
    funnel: list[FunnelStep]


class ScreenCohortComparison(BaseModel):
    screen: str
    drop_off_by_cohort: dict[str, float]
    max_delta: float


class CohortComparisonReport(BaseModel):
    cohorts: list[CohortFunnelSummary]
    screens: list[ScreenCohortComparison]


class ChurnRiskSegment(BaseModel):
    cohort: str
    risk_score: float
    sessions_at_risk: int
    top_reason: str


class Recommendation(BaseModel):
    rank: int
    title: str
    description: str
    expected_lift_pct: float | None


class NodeIntelligence(BaseModel):
    screen: str
    drop_off_rate: float
    avg_seconds_on_node: float | None
    friction_nodes: list[FrictionNode]
    ai_insight: str
    recommendations: list[Recommendation]


def build_cohort_comparison(reports: dict[str, FunnelReport]) -> CohortComparisonReport:
    cohorts = [
        CohortFunnelSummary(
            cohort=cohort, total_sessions=report.total_sessions, funnel=report.funnel
        )
        for cohort, report in reports.items()
    ]

    drop_off_by_screen: dict[str, dict[str, float]] = defaultdict(dict)
    for cohort, report in reports.items():
        for step in report.funnel:
            drop_off_by_screen[step.screen][cohort] = step.drop_off_rate

    screens = [
        ScreenCohortComparison(
            screen=screen,
            drop_off_by_cohort=by_cohort,
            max_delta=(
                (max(by_cohort.values()) - min(by_cohort.values())) if len(by_cohort) > 1 else 0.0
            ),
        )
        for screen, by_cohort in drop_off_by_screen.items()
    ]
    screens.sort(key=lambda s: s.max_delta, reverse=True)

    return CohortComparisonReport(cohorts=cohorts, screens=screens)


async def compare_cohorts(
    session: AsyncSession,
    cohorts: list[str],
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> CohortComparisonReport:
    if not cohorts:
        cohorts = await distinct_cohorts(session, device=device, since=since)

    reports = {
        cohort: await build_funnel_report(session, cohort=cohort, device=device, since=since)
        for cohort in cohorts
    }
    return build_cohort_comparison(reports)


def score_churn_risk(cohort: str, report: FunnelReport) -> ChurnRiskSegment:
    if not report.funnel or report.total_sessions == 0:
        return ChurnRiskSegment(
            cohort=cohort, risk_score=0.0, sessions_at_risk=0, top_reason="No activity recorded."
        )

    mean_drop_off = sum(step.drop_off_rate for step in report.funnel) / len(report.funnel)
    friction_density = min(len(report.friction_nodes) / len(report.funnel), 1.0)
    risk_score = mean_drop_off * CHURN_DROP_OFF_WEIGHT + friction_density * CHURN_FRICTION_WEIGHT

    worst_step = max(report.funnel, key=lambda step: step.drop_off_rate)
    top_reason = (
        f"{worst_step.drop_off_rate * 100:.0f}% drop-off at {worst_step.screen}"
        if worst_step.drop_off_rate > 0
        else "No significant friction detected."
    )

    return ChurnRiskSegment(
        cohort=cohort,
        risk_score=risk_score,
        sessions_at_risk=round(report.total_sessions * risk_score),
        top_reason=top_reason,
    )


async def build_churn_risk_segments(
    session: AsyncSession,
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> list[ChurnRiskSegment]:
    cohorts = await distinct_cohorts(session, device=device, since=since)
    segments = [
        score_churn_risk(
            cohort, await build_funnel_report(session, cohort=cohort, device=device, since=since)
        )
        for cohort in cohorts
    ]
    segments.sort(key=lambda s: s.risk_score, reverse=True)
    return segments


_INSIGHT_TEMPLATES: dict[FrictionKind, str] = {
    FrictionKind.ABNORMAL_DROP_OFF: (
        "{pct:.0f}% of sessions abandon {screen} without continuing -- the largest "
        "drop-off in the discovered funnel."
    ),
    FrictionKind.RAGE_LOOP: (
        "Users are repeatedly interacting with {screen} without progressing, a strong "
        "signal of a confusing or unresponsive control on this screen."
    ),
    FrictionKind.BACKTRACK: (
        "A significant share of sessions retreat from {screen} to an earlier screen, "
        "suggesting this step doesn't match user expectations."
    ),
}

_RECOMMENDATIONS: dict[FrictionKind, list[tuple[str, str, float | None]]] = {
    FrictionKind.ABNORMAL_DROP_OFF: [
        (
            "Simplify the {screen} step",
            "Reduce required fields or decisions on this screen to lower abandonment.",
            14.0,
        ),
        (
            "Add a progress indicator",
            "Show users how many steps remain to reduce perceived effort.",
            8.0,
        ),
    ],
    FrictionKind.RAGE_LOOP: [
        (
            "Audit {screen} for unresponsive controls",
            "Repeated clicks on the same screen usually mean an element looks "
            "interactive but isn't.",
            12.0,
        ),
        (
            "Add inline feedback on interaction",
            "Give immediate visual response to clicks so users don't re-click out of "
            "uncertainty.",
            None,
        ),
    ],
    FrictionKind.BACKTRACK: [
        (
            "Persist form state on {screen}",
            "Prevent data loss when users navigate away and back.",
            10.0,
        ),
        (
            "Clarify navigation affordances",
            "Make it obvious what moving forward from {screen} will do.",
            None,
        ),
    ],
}


def _avg_seconds_on_node(events: list[GraphEvent], screen: str) -> float | None:
    """Mean time between arriving at `screen` and the next event on a
    different screen, per session visit. Sessions that end while still on
    `screen` have no measurable exit and are excluded."""
    by_session: dict[str, list[GraphEvent]] = defaultdict(list)
    for event in events:
        by_session[event.session_id].append(event)

    durations: list[float] = []
    for session_events in by_session.values():
        session_events.sort(key=lambda e: e.timestamp)
        arrival: datetime | None = None
        for event in session_events:
            if event.screen == screen:
                if arrival is None:
                    arrival = event.timestamp
            elif arrival is not None:
                durations.append((event.timestamp - arrival).total_seconds())
                arrival = None

    if not durations:
        return None
    return sum(durations) / len(durations)


def build_node_intelligence(
    screen: str, report: FunnelReport, events: list[GraphEvent]
) -> NodeIntelligence:
    step = next((s for s in report.funnel if s.screen == screen), None)
    nodes_here = [n for n in report.friction_nodes if n.screen == screen]
    dominant = max(nodes_here, key=lambda n: n.sessions_affected, default=None)

    if dominant is not None:
        drop_off_rate = step.drop_off_rate if step is not None else 0.0
        ai_insight = _INSIGHT_TEMPLATES[dominant.kind].format(
            screen=screen, pct=drop_off_rate * 100
        )
    elif step is not None:
        ai_insight = f"{screen} shows no abnormal friction signal in the current data."
    else:
        ai_insight = f"No events recorded for {screen} yet."

    ranked_kinds = sorted(nodes_here, key=lambda n: n.sessions_affected, reverse=True)
    recommendations: list[Recommendation] = []
    seen_kinds: set[FrictionKind] = set()
    for node in ranked_kinds:
        if node.kind in seen_kinds:
            continue
        seen_kinds.add(node.kind)
        for title, description, lift in _RECOMMENDATIONS[node.kind]:
            if len(recommendations) >= MAX_RECOMMENDATIONS:
                break
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    title=title.format(screen=screen),
                    description=description.format(screen=screen),
                    expected_lift_pct=lift,
                )
            )

    return NodeIntelligence(
        screen=screen,
        drop_off_rate=step.drop_off_rate if step is not None else 0.0,
        avg_seconds_on_node=_avg_seconds_on_node(events, screen),
        friction_nodes=nodes_here,
        ai_insight=ai_insight,
        recommendations=recommendations,
    )


async def get_node_intelligence(
    session: AsyncSession,
    screen: str,
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
) -> NodeIntelligence | None:
    events = await query_events(session, cohort=cohort, device=device, since=since)
    funnel = discover_funnel(events)
    if screen not in {step.screen for step in funnel}:
        return None

    friction = detect_friction(events, funnel)
    report = FunnelReport(
        funnel=funnel,
        friction_nodes=friction,
        total_sessions=len({e.session_id for e in events}),
        total_events=len(events),
    )
    return build_node_intelligence(screen, report, events)
