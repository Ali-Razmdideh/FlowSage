"""arq worker: runs simulation jobs pulled off the Redis queue.

Started with `flowsage-worker` (or `arq flowsage_backend.worker.WorkerSettings`),
separately from the API process (`flowsage-backend`/`uvicorn`).
"""

from __future__ import annotations

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
)
from flowsage_backend.config import get_settings
from flowsage_backend.db import create_engine, create_session_factory
from flowsage_backend.integrations.slack import SlackNotConfiguredError, post_slack_message
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.settings import DigestFrequency
from flowsage_backend.retraining import create_retraining_job, execute_retraining
from flowsage_backend.settings_store import get_or_create_calibration_settings
from flowsage_backend.simulations import execute_simulation


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
    per `CalibrationSettings.digest_frequency` -- real dynamic cadence without
    arq's cron spec (fixed at process start) needing to change. Also enqueues
    retraining for anomalous personas when `auto_retrain_on_anomaly` is set,
    same job the manual "Retrain" button in `/calibration` uses."""
    app_settings = get_settings()
    session_factory = ctx["session_factory"]
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        calibration_settings = await get_or_create_calibration_settings(session)
        report = await build_alerts_report(session)

        if calibration_settings.auto_retrain_on_anomaly:
            await _auto_retrain_anomalous_personas(session, report, ctx["redis"])

        interval = _DIGEST_INTERVALS[calibration_settings.digest_frequency]
        last_sent = calibration_settings.digest_last_sent_at
        due = last_sent is None or now - last_sent >= interval
        if not due:
            return

        calibration_settings.digest_last_sent_at = now
        await session.commit()

    try:
        await post_slack_message(
            app_settings.slack_webhook_url,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except SlackNotConfiguredError:
        # No Slack configured -- a background job has no caller to surface this
        # to, unlike POST /alerts/digest/run's 400. Quietly skip.
        pass


async def _auto_retrain_anomalous_personas(
    session: AsyncSession, report: AlertsReport, redis: ArqRedis
) -> None:
    anomalous_persona_names = {alert.persona_name for alert in report.calibration_alerts}
    if not anomalous_persona_names:
        return

    result = await session.execute(select(Persona).where(Persona.name.in_(anomalous_persona_names)))
    personas = result.scalars().all()

    in_flight = await session.execute(
        select(RetrainingJob.persona_id).where(
            RetrainingJob.status.in_((RetrainingStatus.QUEUED, RetrainingStatus.RUNNING))
        )
    )
    persona_ids_in_flight = set(in_flight.scalars().all())

    for persona in personas:
        if persona.id in persona_ids_in_flight:
            continue
        job = await create_retraining_job(session, persona.id)
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
