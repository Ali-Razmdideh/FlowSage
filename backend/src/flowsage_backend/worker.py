"""arq worker: runs simulation jobs pulled off the Redis queue.

Started with `flowsage-worker` (or `arq flowsage_backend.worker.WorkerSettings`),
separately from the API process (`flowsage-backend`/`uvicorn`).
"""

from __future__ import annotations

import uuid
from typing import Any

import arq
from arq import cron
from arq.connections import RedisSettings
from flowsage_predict.vision import AnthropicVisionClient, VisionClient

from flowsage_backend.alerts import build_alerts_report, build_digest_blocks, build_digest_text
from flowsage_backend.config import get_settings
from flowsage_backend.db import create_engine, create_session_factory
from flowsage_backend.integrations.slack import SlackNotConfiguredError, post_slack_message
from flowsage_backend.retraining import execute_retraining
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


async def run_weekly_digest_job(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    session_factory = ctx["session_factory"]
    async with session_factory() as session:
        report = await build_alerts_report(session)

    try:
        await post_slack_message(
            settings.slack_webhook_url,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except SlackNotConfiguredError:
        # No Slack configured -- a background job has no caller to surface this
        # to, unlike POST /alerts/digest/run's 400. Quietly skip.
        pass


class WorkerSettings:
    functions = [run_simulation_job, run_retraining_job]
    cron_jobs = [cron(run_weekly_digest_job, weekday="mon", hour=9, minute=0)]
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
