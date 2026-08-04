import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FrictionKind, FrictionNode, FunnelReport, FunnelStep

from flowsage_backend import insight_cache
from flowsage_backend.churn import (
    NODE_INSIGHT_KIND,
    _avg_seconds_on_node,
    build_cohort_comparison,
    build_node_intelligence,
    generate_and_cache_node_insight,
    get_node_intelligence,
    node_insight_input_hash,
    score_churn_risk,
)
from flowsage_backend.events import ingest_events
from flowsage_backend.models.workspace import Workspace
from flowsage_predict.narrative import NarrativeRecommendation, NodeInsightResult

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


async def _workspace_with_checkout_dropoff(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Node Insight Test", slug=f"node-insight-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.flush()
    now = datetime.now(timezone.utc)
    events = [
        GraphEvent(
            session_id=f"nit-{i}",
            screen="landing",
            event="view",
            timestamp=now,
            device="desktop",
            cohort="default",
        )
        for i in range(5)
    ] + [
        GraphEvent(
            session_id="nit-0",
            screen="checkout",
            event="view",
            timestamp=now,
            device="desktop",
            cohort="default",
        )
    ]
    await ingest_events(session, workspace.id, events)
    await session.commit()
    return workspace.id


async def test_get_node_intelligence_uses_cached_narrative_when_fresh(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    baseline = await get_node_intelligence(db_session, workspace_id, "landing")
    assert baseline is not None
    input_hash = node_insight_input_hash(baseline.drop_off_rate, baseline.friction_nodes)

    await insight_cache.upsert_cached(
        db_session,
        workspace_id,
        NODE_INSIGHT_KIND,
        "landing",
        input_hash,
        {
            "insight": "Cached AI insight.",
            "recommendations": [
                {"title": "Do X", "description": "Because Y.", "expected_lift_pct": 5.0}
            ],
        },
        "claude-haiku-4-5-20251001",
    )

    result = await get_node_intelligence(db_session, workspace_id, "landing")
    assert result is not None
    assert result.ai_insight == "Cached AI insight."
    assert result.recommendations[0].title == "Do X"
    assert result.recommendations[0].rank == 1


async def test_get_node_intelligence_falls_back_to_template_on_stale_cache(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    await insight_cache.upsert_cached(
        db_session,
        workspace_id,
        NODE_INSIGHT_KIND,
        "landing",
        "a-stale-hash-that-will-never-match",
        {"insight": "Stale.", "recommendations": []},
        "claude-haiku-4-5-20251001",
    )

    result = await get_node_intelligence(db_session, workspace_id, "landing")
    assert result is not None
    assert result.ai_insight != "Stale."


class _FakeNarrativeClient:
    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction: list[object]
    ) -> NodeInsightResult:
        return NodeInsightResult(
            insight=f"Generated insight for {screen}",
            recommendations=[
                NarrativeRecommendation(
                    title="Fix it", description="Just fix it.", expected_lift_pct=9.0
                )
            ],
        )

    def generate_calibration_narrative(self, *args: object, **kwargs: object) -> str:
        raise NotImplementedError

    def generate_retraining_rationale(self, *args: object, **kwargs: object) -> str:
        raise NotImplementedError


async def test_generate_and_cache_node_insight_writes_cache_row(db_session: AsyncSession) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    await generate_and_cache_node_insight(
        db_session, workspace_id, "landing", _FakeNarrativeClient()
    )

    cached = await insight_cache.get_cached(db_session, workspace_id, NODE_INSIGHT_KIND, "landing")
    assert cached is not None
    assert cached.payload["insight"] == "Generated insight for landing"


async def test_generate_and_cache_node_insight_swallows_client_errors(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    failing_client = Mock()
    failing_client.generate_node_insight.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        await generate_and_cache_node_insight(db_session, workspace_id, "landing", failing_client)

    assert (
        await insight_cache.get_cached(db_session, workspace_id, NODE_INSIGHT_KIND, "landing")
        is None
    )
    assert "Node insight generation failed" in caplog.text
