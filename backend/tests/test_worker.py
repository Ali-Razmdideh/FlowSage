import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend import worker as worker_module
from flowsage_backend.alerts import AlertsReport, CalibrationAlert
from flowsage_backend.config import Settings
from flowsage_backend.models.calibration import RetrainingJob
from flowsage_backend.models.persona import Persona
from flowsage_backend.settings_store import get_or_create_calibration_settings
from flowsage_backend.worker import run_digest_job

from .conftest import ensure_default_workspace


class _FakeRedis:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, function: str, *args: Any) -> None:
        self.enqueued.append((function, args))


async def _force_due(db_session: AsyncSession) -> Any:
    """`CalibrationSettings` is a session-wide singleton row per workspace (see
    the module docstring gotcha in `conftest.py` re: no per-test DB isolation)
    -- a prior test in this file may have already set `digest_last_sent_at` to
    "now", which would make this test's send look "not due" and silently
    no-op. Force it back to never-sent so this test's send actually fires, and
    hand back the original value so the caller can restore it in a `finally`.

    `run_digest_job` is scoped to the shared "fs-default" workspace (same
    one-workspace rationale as `/v1/events` ingestion), so this test's
    settings row must live there too."""
    workspace_id = await ensure_default_workspace(db_session)
    calibration_settings = await get_or_create_calibration_settings(db_session, workspace_id)
    original_last_sent = calibration_settings.digest_last_sent_at
    calibration_settings.digest_last_sent_at = None
    await db_session.commit()
    return calibration_settings, original_last_sent


async def test_run_digest_job_skips_silently_when_slack_not_configured(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings.slack_webhook_url is None
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)

    calibration_settings, original_last_sent = await _force_due(db_session)
    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}

        # Must not raise -- an unconfigured Slack webhook is a normal, expected
        # state for the digest job to skip quietly (same "not configured" signal
        # the manual /alerts/digest/run endpoint turns into a 400 for a caller
        # who can see it; a background cron job has no caller to show it to).
        await run_digest_job(ctx)
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()


async def test_run_digest_job_posts_when_slack_configured(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.test/x"})
    monkeypatch.setattr(worker_module, "get_settings", lambda: configured)

    posted: dict[str, object] = {}

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        posted["webhook_url"] = webhook_url
        posted["text"] = text

    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    calibration_settings, original_last_sent = await _force_due(db_session)
    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)

        assert posted["webhook_url"] == "https://hooks.slack.test/x"
        assert "FlowSage Digest" in str(posted["text"])
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()


async def test_run_digest_job_skips_send_when_not_due(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A weekly-frequency settings row with a `digest_last_sent_at` from moments
    ago is not due yet -- the daily cron tick must no-op rather than re-send.

    `CalibrationSettings` is a session-wide singleton row (see the module
    docstring gotcha in `conftest.py` re: no per-test DB isolation), so this
    test restores `digest_last_sent_at` afterwards to avoid leaking "not due"
    state into whichever digest test runs next.
    """
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.test/x"})
    monkeypatch.setattr(worker_module, "get_settings", lambda: configured)

    workspace_id = await ensure_default_workspace(db_session)
    calibration_settings = await get_or_create_calibration_settings(db_session, workspace_id)
    original_last_sent = calibration_settings.digest_last_sent_at
    calibration_settings.digest_last_sent_at = datetime.now(timezone.utc)
    await db_session.commit()

    posted = False

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        nonlocal posted
        posted = True

    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)
        assert posted is False
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()


async def test_run_digest_job_auto_retrains_anomalous_personas(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)

    workspace_id = await ensure_default_workspace(db_session)
    calibration_settings = await get_or_create_calibration_settings(db_session, workspace_id)
    original_auto_retrain = calibration_settings.auto_retrain_on_anomaly
    calibration_settings.auto_retrain_on_anomaly = True
    await db_session.commit()

    persona = Persona(
        workspace_id=workspace_id,
        slug=f"worker-autoretrain-{uuid.uuid4().hex[:8]}",
        name="Worker Autoretrain Persona",
        description="d",
        tech_affinity="low",
        primary_device="mobile",
        discovery_mode="search",
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    async def _fake_alerts_report(session: AsyncSession, workspace_id: uuid.UUID) -> AlertsReport:
        return AlertsReport(
            calibration_alerts=[
                CalibrationAlert(persona_name=persona.name, screen="checkout", delta=0.9)
            ],
            churn_alerts=[],
        )

    monkeypatch.setattr(worker_module, "build_alerts_report", _fake_alerts_report)

    try:
        fake_redis = _FakeRedis()
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": fake_redis}
        await run_digest_job(ctx)

        assert len(fake_redis.enqueued) == 1
        assert fake_redis.enqueued[0][0] == "run_retraining_job"

        result = await db_session.execute(
            select(RetrainingJob).where(RetrainingJob.persona_id == persona.id)
        )
        assert result.scalar_one_or_none() is not None
    finally:
        calibration_settings.auto_retrain_on_anomaly = original_auto_retrain
        await db_session.commit()
