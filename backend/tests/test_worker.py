import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend import worker as worker_module
from flowsage_backend.alerts import AlertsReport, CalibrationAlert
from flowsage_backend.models.calibration import RetrainingJob
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.workspace import Workspace
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
    db_session: AsyncSession,
) -> None:
    """No `SlackIntegration` row for fs-default is the default state -- nothing to
    configure here."""
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


async def test_run_digest_job_posts_when_slack_configured(db_session: AsyncSession) -> None:
    from flowsage_backend.models.integration import SlackIntegration

    workspace_id = await ensure_default_workspace(db_session)
    integration = SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/x")
    db_session.add(integration)
    await db_session.commit()

    posted: dict[str, object] = {}

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        posted["webhook_url"] = webhook_url
        posted["text"] = text

    monkeypatch = pytest.MonkeyPatch()
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
        await db_session.delete(integration)
        await db_session.commit()
        monkeypatch.undo()


async def test_run_digest_job_skips_send_when_not_due(db_session: AsyncSession) -> None:
    """A weekly-frequency settings row with a `digest_last_sent_at` from moments
    ago is not due yet -- the daily cron tick must no-op rather than re-send.

    `CalibrationSettings` is a session-wide singleton row (see the module
    docstring gotcha in `conftest.py` re: no per-test DB isolation), so this
    test restores `digest_last_sent_at` afterwards to avoid leaking "not due"
    state into whichever digest test runs next.
    """
    from flowsage_backend.models.integration import SlackIntegration

    workspace_id = await ensure_default_workspace(db_session)
    integration = SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/x")
    db_session.add(integration)
    calibration_settings = await get_or_create_calibration_settings(db_session, workspace_id)
    original_last_sent = calibration_settings.digest_last_sent_at
    calibration_settings.digest_last_sent_at = datetime.now(timezone.utc)
    await db_session.commit()

    posted = False

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        nonlocal posted
        posted = True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)
        assert posted is False
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()
        await db_session.delete(integration)
        await db_session.commit()
        monkeypatch.undo()


async def test_run_digest_job_auto_retrains_anomalous_personas(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
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


async def test_run_digest_job_delivers_to_two_workspaces_independently(
    db_session: AsyncSession,
) -> None:
    """Two fresh workspaces (never touched by any other test, so no shared-state
    dance needed), each with its own Slack webhook and its own enabled Webhook,
    each with its own churn-risk-triggering events. After one `run_digest_job`
    call, each workspace's webhook has exactly one delivery -- scoped assertions
    via `list_deliveries(webhook_id)`, so however many *other* leftover workspaces
    also get processed in the same run is irrelevant."""
    from flowsage_backend.models.event import Event
    from flowsage_backend.models.integration import SlackIntegration
    from flowsage_backend.models.webhook import Webhook
    from flowsage_backend.webhooks_store import list_deliveries

    async def _make_workspace_with_alerts(cohort: str) -> tuple[uuid.UUID, Webhook]:
        workspace = Workspace(name=f"Digest Test {cohort}", slug=f"digest-{cohort}-{uuid.uuid4().hex[:8]}")
        db_session.add(workspace)
        await db_session.commit()
        await db_session.refresh(workspace)

        db_session.add(
            SlackIntegration(workspace_id=workspace.id, webhook_url=f"https://hooks.slack.test/{cohort}")
        )
        webhook = Webhook(
            workspace_id=workspace.id,
            url=f"https://example.test/{cohort}",
            secret="s3cr3t",
            event_types=["alert.triggered"],
        )
        db_session.add(webhook)

        base = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)
        session_ids = [f"{cohort}-{i}" for i in range(8)]
        for sid in session_ids:
            db_session.add(
                Event(
                    workspace_id=workspace.id, session_id=sid, screen="landing", event="screen_view",
                    timestamp=base, device="mobile", cohort=cohort,
                )
            )
        for sid in session_ids[:2]:
            db_session.add(
                Event(
                    workspace_id=workspace.id, session_id=sid, screen="checkout", event="screen_view",
                    timestamp=base, device="mobile", cohort=cohort,
                )
            )
        db_session.add(
            Event(
                workspace_id=workspace.id, session_id=session_ids[0], screen="confirmation",
                event="screen_view", timestamp=base, device="mobile", cohort=cohort,
            )
        )
        await db_session.commit()
        await db_session.refresh(webhook)
        return workspace.id, webhook

    async def _fake_deliver(
        url: str, *, secret: str, event_type: str, payload: dict[str, object]
    ) -> tuple[int, bool]:
        return 200, True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "deliver_webhook", _fake_deliver)

    workspace_a_id, webhook_a = await _make_workspace_with_alerts("digestcohorta")
    workspace_b_id, webhook_b = await _make_workspace_with_alerts("digestcohortb")

    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)

        deliveries_a = await list_deliveries(db_session, webhook_a.id)
        deliveries_b = await list_deliveries(db_session, webhook_b.id)
        assert len(deliveries_a) == 1
        assert len(deliveries_b) == 1
        assert json.loads(deliveries_a[0].payload)["churn_alerts"][0]["cohort"] == "digestcohorta"
        assert json.loads(deliveries_b[0].payload)["churn_alerts"][0]["cohort"] == "digestcohortb"
    finally:
        monkeypatch.undo()
