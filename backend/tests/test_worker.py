from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend import worker as worker_module
from flowsage_backend.config import Settings
from flowsage_backend.worker import run_weekly_digest_job


async def test_run_weekly_digest_job_skips_silently_when_slack_not_configured(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings.slack_webhook_url is None
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)

    ctx: dict[str, Any] = {"session_factory": lambda: db_session}

    # Must not raise -- an unconfigured Slack webhook is a normal, expected
    # state for the digest job to skip quietly (same "not configured" signal
    # the manual /alerts/digest/run endpoint turns into a 400 for a caller who
    # can see it; a background cron job has no caller to show it to).
    await run_weekly_digest_job(ctx)


async def test_run_weekly_digest_job_posts_when_slack_configured(
    db_session: AsyncSession, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = settings.model_copy(update={"slack_webhook_url": "https://hooks.slack.test/x"})
    monkeypatch.setattr(worker_module, "get_settings", lambda: configured)

    posted: dict[str, object] = {}

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        posted["webhook_url"] = webhook_url
        posted["text"] = text

    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    ctx: dict[str, Any] = {"session_factory": lambda: db_session}
    await run_weekly_digest_job(ctx)

    assert posted["webhook_url"] == "https://hooks.slack.test/x"
    assert "FlowSage Weekly Digest" in str(posted["text"])
