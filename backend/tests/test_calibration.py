from datetime import datetime, timezone

from flowsage_graph.models import FunnelStep
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.calibration import (
    bucket_severity,
    build_calibration_report,
    build_screen_calibrations,
    latest_completed_run_for_persona,
    latest_completed_runs_by_persona,
    predicted_scores_by_screen,
)
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.seed import seed_baseline_personas


def test_bucket_severity_maps_known_values() -> None:
    assert bucket_severity("low") == 0.2
    assert bucket_severity("medium") == 0.45
    assert bucket_severity("high") == 0.7
    assert bucket_severity("critical") == 0.9


def test_bucket_severity_unknown_defaults_to_zero() -> None:
    assert bucket_severity("nonsense") == 0.0


def test_predicted_scores_by_screen_takes_max_per_screen() -> None:
    issues = [
        FrictionIssue(
            screen="cart",
            severity="low",
            title="a",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        ),
        FrictionIssue(
            screen="cart",
            severity="high",
            title="b",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        ),
        FrictionIssue(
            screen="checkout",
            severity="medium",
            title="c",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        ),
    ]

    scores = predicted_scores_by_screen(issues)

    assert scores == {"cart": 0.7, "checkout": 0.45}


def test_build_screen_calibrations_flags_anomaly_above_threshold() -> None:
    predicted = {"cart": 0.2, "checkout": 0.45}
    funnel = [
        FunnelStep(screen="cart", sessions_entered=10, sessions_continued=8),  # drop 0.2
        FunnelStep(screen="checkout", sessions_entered=10, sessions_continued=1),  # drop 0.9
    ]

    results = build_screen_calibrations(predicted, funnel)

    by_screen = {r.screen: r for r in results}
    assert by_screen["cart"].anomaly is False
    assert by_screen["checkout"].anomaly is True
    assert by_screen["checkout"].delta == 0.9 - 0.45


def test_build_screen_calibrations_ignores_screens_without_a_prediction() -> None:
    predicted = {"cart": 0.2}
    funnel = [
        FunnelStep(screen="cart", sessions_entered=10, sessions_continued=8),
        FunnelStep(screen="unrelated_screen", sessions_entered=10, sessions_continued=0),
    ]

    results = build_screen_calibrations(predicted, funnel)

    assert [r.screen for r in results] == ["cart"]


async def _completed_run_with_issue(
    session: AsyncSession, persona: Persona, *, screen: str, severity: str
) -> SimulationRun:
    run = SimulationRun(
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    session.add(
        FrictionIssue(
            run_id=run.id,
            screen=screen,
            severity=severity,
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    await session.commit()
    await session.refresh(run)
    return run


async def test_latest_completed_run_for_persona_picks_most_recent(
    db_session: AsyncSession,
) -> None:
    personas = await seed_baseline_personas(db_session)
    persona = personas[0]
    older = await _completed_run_with_issue(db_session, persona, screen="a", severity="low")
    older.finished_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await db_session.commit()
    newer = await _completed_run_with_issue(db_session, persona, screen="b", severity="high")

    result = await latest_completed_run_for_persona(db_session, persona.id)

    assert result is not None
    assert result.id == newer.id


async def test_latest_completed_runs_by_persona_one_per_persona(
    db_session: AsyncSession,
) -> None:
    personas = await seed_baseline_personas(db_session)
    await _completed_run_with_issue(db_session, personas[0], screen="a", severity="low")
    await _completed_run_with_issue(db_session, personas[1], screen="b", severity="high")

    runs = await latest_completed_runs_by_persona(db_session)

    persona_ids = {run.persona_id for run in runs}
    assert personas[0].id in persona_ids
    assert personas[1].id in persona_ids


async def test_build_calibration_report_flags_anomaly_and_computes_accuracy(
    db_session: AsyncSession,
) -> None:
    # This suite's Postgres fixture is session-scoped (see conftest.py) -- rows
    # from other tests/files persist. So assertions here are scoped to *this*
    # test's own persona/run rather than the report's total size, which may
    # include leftovers from tests that ran earlier in the same session.
    personas = await seed_baseline_personas(db_session)
    persona = personas[0]
    await _completed_run_with_issue(db_session, persona, screen="checkout", severity="low")
    funnel = [FunnelStep(screen="checkout", sessions_entered=10, sessions_continued=1)]

    report = await build_calibration_report(db_session, funnel)

    assert report.has_anomaly is True
    persona_calibration = next(p for p in report.personas if p.persona_id == str(persona.id))
    assert persona_calibration.screens[0].anomaly is True
    assert any(a.persona_id == str(persona.id) for a in report.accuracy_points)


async def test_build_calibration_report_skips_personas_without_predictions(
    db_session: AsyncSession,
) -> None:
    # `personas[-1]` never receives a completed run in any test in this suite,
    # so its absence from the report is a reliable signal even with a shared,
    # un-truncated test database (see comment above).
    personas = await seed_baseline_personas(db_session)
    untouched = personas[-1]

    report = await build_calibration_report(db_session, funnel=[])

    assert all(p.persona_id != str(untouched.id) for p in report.personas)
    assert all(a.persona_id != str(untouched.id) for a in report.accuracy_points)
