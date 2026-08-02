import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.alerts import (
    AlertsReport,
    CalibrationAlert,
    ChurnAlert,
    build_digest_blocks,
    build_digest_text,
    check_calibration_anomalies,
    check_churn_alerts,
    has_alerts,
)
from flowsage_backend.calibration import CalibrationReport, PersonaCalibration, ScreenCalibration
from flowsage_backend.churn import ChurnRiskSegment
from flowsage_backend.models.workspace import Workspace


async def _create_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


def test_check_calibration_anomalies_returns_only_flagged_screens() -> None:
    report = CalibrationReport(
        personas=[
            PersonaCalibration(
                persona_id="p1",
                persona_name="Novice Nora",
                run_id="r1",
                screens=[
                    ScreenCalibration(
                        screen="checkout",
                        predicted_score=0.2,
                        observed_score=0.9,
                        delta=0.7,
                        anomaly=True,
                    ),
                    ScreenCalibration(
                        screen="landing",
                        predicted_score=0.2,
                        observed_score=0.25,
                        delta=0.05,
                        anomaly=False,
                    ),
                ],
            )
        ],
        accuracy_points=[],
        has_anomaly=True,
    )

    alerts = check_calibration_anomalies(report)

    assert len(alerts) == 1
    assert alerts[0].screen == "checkout"
    assert alerts[0].persona_name == "Novice Nora"


def test_check_churn_alerts_filters_by_threshold() -> None:
    segments = [
        ChurnRiskSegment(cohort="at_risk", risk_score=0.72, sessions_at_risk=5, top_reason="x"),
        ChurnRiskSegment(cohort="healthy", risk_score=0.1, sessions_at_risk=0, top_reason="y"),
    ]

    alerts = check_churn_alerts(segments)

    assert len(alerts) == 1
    assert alerts[0].cohort == "at_risk"


def test_has_alerts_true_when_either_list_nonempty() -> None:
    empty = AlertsReport(calibration_alerts=[], churn_alerts=[], friction_regression_alerts=[])
    assert has_alerts(empty) is False

    with_churn = AlertsReport(
        calibration_alerts=[],
        churn_alerts=[ChurnAlert(cohort="c", risk_score=0.9, top_reason="r")],
        friction_regression_alerts=[],
    )
    assert has_alerts(with_churn) is True


def test_build_digest_text_no_alerts() -> None:
    report = AlertsReport(calibration_alerts=[], churn_alerts=[], friction_regression_alerts=[])
    text = build_digest_text(report)
    assert "no calibration, churn, or friction-regression alerts" in text.lower()


def test_build_digest_blocks_includes_a_block_per_alert() -> None:
    report = AlertsReport(
        calibration_alerts=[CalibrationAlert(persona_name="Nora", screen="checkout", delta=0.7)],
        churn_alerts=[ChurnAlert(cohort="at_risk", risk_score=0.72, top_reason="drop-off")],
        friction_regression_alerts=[],
    )

    blocks = build_digest_blocks(report)

    joined = " ".join(str(b) for b in blocks)
    assert "checkout" in joined
    assert "at_risk" in joined


async def test_check_friction_regression_alerts_flags_score_jump(db_session: AsyncSession) -> None:
    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
    from flowsage_backend.models.simulation import (
        FrictionIssue,
        RunStatus,
        SimulationRun,
        SimulationStep,
    )
    from flowsage_backend.alerts import check_friction_regression_alerts

    workspace_id = await _create_workspace(db_session)
    persona = Persona(
        workspace_id=workspace_id,
        slug="test-persona",
        name="Test Persona",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Mobile",
        discovery_mode="Search-driven",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        interval=ScheduleInterval.DAILY,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    async def _completed_run_with_severity(severity: str) -> SimulationRun:
        run = SimulationRun(
            workspace_id=workspace_id,
            flow_name="Checkout",
            goal="Complete purchase",
            persona_id=persona.id,
            screenshots_dir="/tmp/irrelevant",
            status=RunStatus.COMPLETED,
            scheduled_simulation_id=config.id,
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(
            SimulationStep(
                workspace_id=workspace_id,
                run_id=run.id,
                sequence=0,
                screen="cart",
                action="a",
                reasoning="r",
            )
        )
        db_session.add(
            FrictionIssue(
                workspace_id=workspace_id,
                run_id=run.id,
                screen="cart",
                severity=severity,
                title="t",
                heuristic_violated="h",
                persona_impact="p",
                description="d",
                suggested_fix="f",
            )
        )
        await db_session.commit()
        return run

    await _completed_run_with_severity("low")  # score 0.2, previous
    await _completed_run_with_severity("critical")  # score 0.9, current -- delta 0.7

    alerts = await check_friction_regression_alerts(db_session, workspace_id)

    assert len(alerts) == 1
    assert alerts[0].scheduled_simulation_id == config.id
    assert alerts[0].previous_score == 0.2
    assert alerts[0].current_score == 0.9
    assert round(alerts[0].delta, 2) == 0.7


async def test_check_friction_regression_alerts_ignores_delta_just_under_threshold(
    db_session: AsyncSession,
) -> None:
    """Two screens per run, averaged: previous (low, medium) -> 0.325; current
    (medium, medium) -> 0.45. Delta 0.125 sits just under
    FRICTION_REGRESSION_ALERT_THRESHOLD (0.15) -- must not fire."""
    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
    from flowsage_backend.models.simulation import (
        FrictionIssue,
        RunStatus,
        SimulationRun,
        SimulationStep,
    )
    from flowsage_backend.alerts import check_friction_regression_alerts

    workspace_id = await _create_workspace(db_session)
    persona = Persona(
        workspace_id=workspace_id,
        slug="test-persona-2",
        name="Test Persona 2",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Mobile",
        discovery_mode="Search-driven",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        interval=ScheduleInterval.DAILY,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    async def _completed_run_with_severities(severities: dict[str, str]) -> None:
        run = SimulationRun(
            workspace_id=workspace_id,
            flow_name="Checkout",
            goal="Complete purchase",
            persona_id=persona.id,
            screenshots_dir="/tmp/irrelevant",
            status=RunStatus.COMPLETED,
            scheduled_simulation_id=config.id,
        )
        db_session.add(run)
        await db_session.flush()
        for i, screen in enumerate(severities):
            db_session.add(
                SimulationStep(
                    workspace_id=workspace_id,
                    run_id=run.id,
                    sequence=i,
                    screen=screen,
                    action="a",
                    reasoning="r",
                )
            )
        for screen, severity in severities.items():
            db_session.add(
                FrictionIssue(
                    workspace_id=workspace_id,
                    run_id=run.id,
                    screen=screen,
                    severity=severity,
                    title="t",
                    heuristic_violated="h",
                    persona_impact="p",
                    description="d",
                    suggested_fix="f",
                )
            )
        await db_session.commit()

    await _completed_run_with_severities({"cart": "low", "pay": "medium"})  # avg 0.325, previous
    await _completed_run_with_severities({"cart": "medium", "pay": "medium"})  # avg 0.45, current

    alerts = await check_friction_regression_alerts(db_session, workspace_id)
    assert alerts == []
