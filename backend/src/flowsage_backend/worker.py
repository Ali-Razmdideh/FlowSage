"""arq worker: runs simulation jobs pulled off the Redis queue.

Started with `flowsage-worker` (or `arq flowsage_backend.worker.WorkerSettings`),
separately from the API process (`flowsage-backend`/`uvicorn`).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import arq
from arq import cron
from arq.connections import ArqRedis, RedisSettings
from flowsage_predict.vision import AnthropicVisionClient, VisionClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend import scheduled_simulations
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
from flowsage_backend.models.audit_log import AuditLog
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.event import Event
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation
from flowsage_backend.models.settings import DigestFrequency
from flowsage_backend.models.simulation import RunStatus, SimulationRun
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
            await record_delivery(
                session, webhook.id, "alert.triggered", payload, status_code, success
            )


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


async def run_retention_purge_job(ctx: dict[str, Any]) -> None:
    """Fires daily. Enforces each workspace's own `retention_days` against
    `AuditLog` and `Event` -- the two unbounded-growth tables this chunk's spec
    calls out -- and also reclaims stale scheduled-simulation screenshot
    directories on disk (see `_purge_stale_scheduled_screenshots`). One
    workspace's failure doesn't block the others, mirroring run_digest_job's
    per-workspace loop."""
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        result = await session.execute(select(Workspace.id, Workspace.retention_days))
        workspaces = list(result.all())

    upload_dir = Path(get_settings().upload_dir)
    for workspace_id, retention_days in workspaces:
        try:
            async with session_factory() as session:
                await _purge_workspace_retention(session, workspace_id, retention_days)
            async with session_factory() as session:
                await _purge_stale_scheduled_screenshots(
                    session, workspace_id, retention_days, upload_dir
                )
        except Exception:  # noqa: BLE001 - one workspace's purge failure must not
            # stop the retention job from running for every other workspace.
            logger.warning("Retention purge failed for workspace %s", workspace_id, exc_info=True)


async def _purge_workspace_retention(
    session: AsyncSession, workspace_id: uuid.UUID, retention_days: int
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    await session.execute(
        delete(AuditLog).where(AuditLog.workspace_id == workspace_id, AuditLog.created_at < cutoff)
    )
    await session.execute(
        delete(Event).where(Event.workspace_id == workspace_id, Event.timestamp < cutoff)
    )
    await session.commit()


async def _purge_stale_scheduled_screenshots(
    session: AsyncSession, workspace_id: uuid.UUID, retention_days: int, upload_dir: Path
) -> None:
    """Deletes on-disk screenshot-push directories under
    `<upload_dir>/scheduled/<config_id>/` older than the workspace's retention
    window. A directory is protected (never deleted here, regardless of age) if
    it's a config's current `pending_screenshots_dir` (not yet fired) or a
    QUEUED/RUNNING run's `screenshots_dir` (still being read by the worker) --
    everything else is either an already-fired run's now-durable-elsewhere
    input, or an orphan from a lost concurrent-push race, both safe to reclaim."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    configs = (
        await session.execute(
            select(ScheduledSimulation.id, ScheduledSimulation.pending_screenshots_dir).where(
                ScheduledSimulation.workspace_id == workspace_id
            )
        )
    ).all()
    if not configs:
        return

    in_flight_dirs = set(
        (
            await session.execute(
                select(SimulationRun.screenshots_dir).where(
                    SimulationRun.scheduled_simulation_id.in_([c.id for c in configs]),
                    SimulationRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
                )
            )
        )
        .scalars()
        .all()
    )
    protected_dirs = in_flight_dirs | {
        c.pending_screenshots_dir for c in configs if c.pending_screenshots_dir
    }

    for config_id, _ in configs:
        config_root = upload_dir / "scheduled" / str(config_id)
        if not config_root.is_dir():
            continue
        for push_dir in config_root.iterdir():
            if not push_dir.is_dir() or str(push_dir) in protected_dirs:
                continue
            modified_at = datetime.fromtimestamp(push_dir.stat().st_mtime, tz=timezone.utc)
            if modified_at < cutoff:
                shutil.rmtree(push_dir, ignore_errors=True)


async def run_scheduled_simulations_job(ctx: dict[str, Any]) -> None:
    """Fires hourly; the actual daily/weekly/on_push cadence is decided per-config
    inside fire_due_scheduled_simulations (same 'cron fixed, cadence in application
    logic' pattern as run_digest_job). One workspace's failure doesn't block the
    others, mirroring run_digest_job's and run_retention_purge_job's per-workspace
    loops. Each newly-fired run still needs its QUEUED->RUNNING->COMPLETED walk,
    so this enqueues the existing run_simulation_job for it exactly like the
    one-shot upload endpoint does."""
    session_factory = ctx["session_factory"]
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        result = await session.execute(select(Workspace.id).where(Workspace.archived.is_(False)))
        workspace_ids = list(result.scalars().all())

    for workspace_id in workspace_ids:
        try:
            async with session_factory() as session:
                fired_runs = await scheduled_simulations.fire_due_scheduled_simulations(
                    session, workspace_id, now
                )
            for run in fired_runs:
                await ctx["redis"].enqueue_job("run_simulation_job", str(run.id))
        except Exception:  # noqa: BLE001 - one workspace's failure must not stop
            # the scheduled-simulations job from running for every other workspace.
            logger.warning(
                "Scheduled simulations job failed for workspace %s", workspace_id, exc_info=True
            )


class WorkerSettings:
    functions = [run_simulation_job, run_retraining_job]
    cron_jobs = [
        cron(run_digest_job, hour=9, minute=0),
        cron(run_retention_purge_job, hour=3, minute=0),
        cron(run_scheduled_simulations_job, minute=0),
    ]
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
