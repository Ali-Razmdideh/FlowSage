"""arq worker: runs simulation jobs pulled off the Redis queue.

Started with `flowsage-worker` (or `arq flowsage_backend.worker.WorkerSettings`),
separately from the API process (`flowsage-backend`/`uvicorn`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import arq
from arq import cron
from arq.connections import ArqRedis, RedisSettings
from flowsage_predict.vision import AnthropicVisionClient, VisionClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.alerts import (
    AlertsReport,
    build_alerts_report,
    build_digest_blocks,
    build_digest_text,
    has_alerts,
)
from flowsage_backend.config import get_settings
from flowsage_backend.db import create_engine, create_session_factory
from flowsage_backend.integrations.slack import post_slack_message
from flowsage_backend.integrations.webhooks import deliver_webhook
from flowsage_backend.integrations_store import get_slack_integration
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.settings import DigestFrequency
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.retraining import create_retraining_job, execute_retraining
from flowsage_backend.settings_store import get_or_create_calibration_settings
from flowsage_backend.simulations import execute_simulation
from flowsage_backend.webhooks_store import list_enabled_webhooks_for_event, record_delivery

logger = logging.getLogger(__name__)


async def _startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    ctx["vision_client"] = AnthropicVisionClient()


async def _shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


async def run_simulation_job(ctx: dict[str, Any], run_id: str) -> None:
    session_factory = ctx["session_factory"]
    vision_client: VisionClient = ctx["vision_client"]
    async with session_factory() as session:
        await execute_simulation(session, uuid.UUID(run_id), vision_client)


async def run_retraining_job(ctx: dict[str, Any], job_id: str) -> None:
    session_factory = ctx["session_factory"]
    async with session_factory() as session:
        await execute_retraining(session, uuid.UUID(job_id))


_DIGEST_INTERVALS = {
    DigestFrequency.DAILY: timedelta(days=1),
    DigestFrequency.WEEKLY: timedelta(days=7),
}


async def run_digest_job(ctx: dict[str, Any]) -> None:
    """Fires daily off the cron schedule below, but only actually sends when due
    per each workspace's own `CalibrationSettings.digest_frequency` -- real dynamic
    cadence without arq's cron spec (fixed at process start) needing to change.
    Iterates every non-archived workspace independently: one workspace's Slack
    failure/missing config doesn't stop the others (broad except -- a bad webhook
    URL in one workspace must never abort digests for every other workspace in
    the loop). Also delivers to each workspace's enabled `Webhook` rows when
    `has_alerts(report)` is true."""
    session_factory = ctx["session_factory"]
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        result = await session.execute(select(Workspace.id).where(Workspace.archived.is_(False)))
        workspace_ids = list(result.scalars().all())

    for workspace_id in workspace_ids:
        await _run_digest_for_workspace(ctx, workspace_id, now)


async def _run_digest_for_workspace(
    ctx: dict[str, Any], workspace_id: uuid.UUID, now: datetime
) -> None:
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        calibration_settings = await get_or_create_calibration_settings(session, workspace_id)
        report = await build_alerts_report(session, workspace_id)

        if calibration_settings.auto_retrain_on_anomaly:
            await _auto_retrain_anomalous_personas(session, workspace_id, report, ctx["redis"])

        interval = _DIGEST_INTERVALS[calibration_settings.digest_frequency]
        last_sent = calibration_settings.digest_last_sent_at
        due = last_sent is None or now - last_sent >= interval
        if not due:
            return

        calibration_settings.digest_last_sent_at = now
        await session.commit()

        integration = await get_slack_integration(session, workspace_id)

    try:
        await post_slack_message(
            integration.webhook_url if integration else None,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except Exception:  # noqa: BLE001 - one workspace's broken/unreachable Slack
        # config must not abort the loop over every other workspace.
        logger.warning("Digest Slack delivery failed for workspace %s", workspace_id, exc_info=True)

    if not has_alerts(report):
        return

    async with session_factory() as session:
        webhooks = await list_enabled_webhooks_for_event(session, workspace_id, "alert.triggered")
        payload = report.model_dump(mode="json")
        for webhook in webhooks:
            status_code, success = await deliver_webhook(
                webhook.url, secret=webhook.secret, event_type="alert.triggered", payload=payload
            )
            await record_delivery(session, webhook.id, "alert.triggered", payload, status_code, success)


async def _auto_retrain_anomalous_personas(
    session: AsyncSession, workspace_id: uuid.UUID, report: AlertsReport, redis: ArqRedis
) -> None:
    anomalous_persona_names = {alert.persona_name for alert in report.calibration_alerts}
    if not anomalous_persona_names:
        return

    result = await session.execute(
        select(Persona).where(
            Persona.workspace_id == workspace_id, Persona.name.in_(anomalous_persona_names)
        )
    )
    personas = result.scalars().all()

    in_flight = await session.execute(
        select(RetrainingJob.persona_id).where(
            RetrainingJob.workspace_id == workspace_id,
            RetrainingJob.status.in_((RetrainingStatus.QUEUED, RetrainingStatus.RUNNING)),
        )
    )
    persona_ids_in_flight = set(in_flight.scalars().all())

    for persona in personas:
        if persona.id in persona_ids_in_flight:
            continue
        job = await create_retraining_job(session, persona.id, workspace_id=workspace_id)
        await redis.enqueue_job("run_retraining_job", str(job.id))


class WorkerSettings:
    functions = [run_simulation_job, run_retraining_job]
    cron_jobs = [cron(run_digest_job, hour=9, minute=0)]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)


def run_worker() -> None:
    # arq's WorkerCoroutine protocol types job functions as (ctx, *args, **kwargs),
    # so a function with a concrete signature like run_simulation_job's never
    # structurally matches it -- a known arq typing limitation, not a real bug here.
    arq.run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    run_worker()
