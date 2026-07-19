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
    empty = AlertsReport(calibration_alerts=[], churn_alerts=[])
    assert has_alerts(empty) is False

    with_churn = AlertsReport(
        calibration_alerts=[],
        churn_alerts=[ChurnAlert(cohort="c", risk_score=0.9, top_reason="r")],
    )
    assert has_alerts(with_churn) is True


def test_build_digest_text_no_alerts() -> None:
    report = AlertsReport(calibration_alerts=[], churn_alerts=[])
    text = build_digest_text(report)
    assert "no calibration or churn alerts" in text.lower()


def test_build_digest_blocks_includes_a_block_per_alert() -> None:
    report = AlertsReport(
        calibration_alerts=[CalibrationAlert(persona_name="Nora", screen="checkout", delta=0.7)],
        churn_alerts=[ChurnAlert(cohort="at_risk", risk_score=0.72, top_reason="drop-off")],
    )

    blocks = build_digest_blocks(report)

    joined = " ".join(str(b) for b in blocks)
    assert "checkout" in joined
    assert "at_risk" in joined
