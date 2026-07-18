from datetime import datetime, timezone

from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FrictionKind, FrictionNode, FunnelReport, FunnelStep

from flowsage_backend.churn import (
    _avg_seconds_on_node,
    build_cohort_comparison,
    build_node_intelligence,
    score_churn_risk,
)

_T0 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _event(session_id: str, screen: str, seconds: float) -> GraphEvent:
    return GraphEvent(
        session_id=session_id,
        screen=screen,
        event="view",
        timestamp=datetime.fromtimestamp(_T0.timestamp() + seconds, tz=timezone.utc),
    )


def test_build_cohort_comparison_ranks_screens_by_max_delta() -> None:
    reports = {
        "paid": FunnelReport(
            funnel=[
                FunnelStep(screen="landing", sessions_entered=10, sessions_continued=9),
                FunnelStep(screen="checkout", sessions_entered=9, sessions_continued=8),
            ],
            friction_nodes=[],
            total_sessions=10,
            total_events=20,
        ),
        "trial": FunnelReport(
            funnel=[
                FunnelStep(screen="landing", sessions_entered=10, sessions_continued=9),
                FunnelStep(screen="checkout", sessions_entered=9, sessions_continued=1),
            ],
            friction_nodes=[],
            total_sessions=10,
            total_events=20,
        ),
    }

    result = build_cohort_comparison(reports)

    assert {c.cohort for c in result.cohorts} == {"paid", "trial"}
    assert result.screens[0].screen == "checkout"
    assert result.screens[0].max_delta > result.screens[1].max_delta


def test_build_cohort_comparison_single_cohort_has_zero_delta() -> None:
    reports = {
        "paid": FunnelReport(
            funnel=[FunnelStep(screen="landing", sessions_entered=10, sessions_continued=9)],
            friction_nodes=[],
            total_sessions=10,
            total_events=10,
        ),
    }

    result = build_cohort_comparison(reports)

    assert result.screens[0].max_delta == 0.0


def test_score_churn_risk_no_activity_is_zero_risk() -> None:
    report = FunnelReport(funnel=[], friction_nodes=[], total_sessions=0, total_events=0)

    segment = score_churn_risk("empty_cohort", report)

    assert segment.risk_score == 0.0
    assert segment.sessions_at_risk == 0


def test_score_churn_risk_weights_drop_off_and_friction_density() -> None:
    report = FunnelReport(
        funnel=[
            FunnelStep(screen="landing", sessions_entered=10, sessions_continued=2),
        ],
        friction_nodes=[
            FrictionNode(
                screen="landing",
                kind=FrictionKind.ABNORMAL_DROP_OFF,
                detail="detail",
                sessions_affected=8,
            )
        ],
        total_sessions=10,
        total_events=10,
    )

    segment = score_churn_risk("risky_cohort", report)

    # mean_drop_off = 0.8, friction_density = min(1/1, 1.0) = 1.0
    # risk = 0.8*0.6 + 1.0*0.4 = 0.88
    assert round(segment.risk_score, 2) == 0.88
    assert segment.sessions_at_risk == round(10 * 0.88)
    assert "landing" in segment.top_reason


def test_avg_seconds_on_node_measures_dwell_between_transitions() -> None:
    events = [
        _event("s1", "landing", 0),
        _event("s1", "checkout", 10),
        _event("s2", "landing", 0),
        _event("s2", "checkout", 20),
    ]

    avg = _avg_seconds_on_node(events, "landing")

    assert avg == 15.0


def test_avg_seconds_on_node_ignores_sessions_that_never_leave() -> None:
    events = [_event("s1", "landing", 0), _event("s1", "landing", 5)]

    assert _avg_seconds_on_node(events, "landing") is None


def test_build_node_intelligence_generates_insight_and_recommendations() -> None:
    report = FunnelReport(
        funnel=[
            FunnelStep(screen="landing", sessions_entered=10, sessions_continued=9),
            FunnelStep(screen="checkout", sessions_entered=9, sessions_continued=1),
        ],
        friction_nodes=[
            FrictionNode(
                screen="checkout",
                kind=FrictionKind.ABNORMAL_DROP_OFF,
                detail="detail",
                sessions_affected=8,
            )
        ],
        total_sessions=10,
        total_events=20,
    )
    events = [_event("s1", "checkout", 0), _event("s1", "confirmation", 30)]

    intelligence = build_node_intelligence("checkout", report, events)

    assert intelligence.drop_off_rate > 0.5
    assert "checkout" in intelligence.ai_insight
    assert len(intelligence.recommendations) > 0
    assert intelligence.recommendations[0].rank == 1
    assert intelligence.avg_seconds_on_node == 30.0


def test_build_node_intelligence_no_friction_gives_calm_insight() -> None:
    report = FunnelReport(
        funnel=[FunnelStep(screen="landing", sessions_entered=10, sessions_continued=10)],
        friction_nodes=[],
        total_sessions=10,
        total_events=10,
    )

    intelligence = build_node_intelligence("landing", report, [])

    assert intelligence.recommendations == []
    assert "no abnormal friction" in intelligence.ai_insight
