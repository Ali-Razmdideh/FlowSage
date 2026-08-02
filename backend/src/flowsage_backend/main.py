"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import arq
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from flowsage_graph.ingest import Neo4jGraphSink
from sqlalchemy import text

from flowsage_backend.api.alerts import router as alerts_router
from flowsage_backend.api.audit import router as audit_router
from flowsage_backend.api.auth import router as auth_router
from flowsage_backend.api.billing import router as billing_router
from flowsage_backend.api.calibration import router as calibration_router
from flowsage_backend.api.events import events_router, graph_router
from flowsage_backend.api.exports import router as exports_router
from flowsage_backend.api.insights import insights_router
from flowsage_backend.api.integrations import router as integrations_router
from flowsage_backend.api.onboarding import router as onboarding_router
from flowsage_backend.api.personas import router as personas_router
from flowsage_backend.api.scheduled_simulations import router as scheduled_simulations_router
from flowsage_backend.api.settings import router as settings_router
from flowsage_backend.api.simulations import router as simulations_router
from flowsage_backend.api.workspaces import router as workspaces_router
from flowsage_backend.config import Settings, get_settings
from flowsage_backend.db import create_engine, create_session_factory
from flowsage_backend.rate_limit import configure_rate_limiting


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.arq_pool = await arq.create_pool(RedisSettings.from_dsn(app.state.settings.redis_url))
    yield
    await app.state.arq_pool.aclose()
    await asyncio.to_thread(app.state.graph_sink.close)
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="FlowSage API",
        description=(
            "FlowSage's predictive & observed UX intelligence platform. "
            "`/v1/insights/*` endpoints are public, API-key-authenticated "
            "(`X-API-Key` header) read endpoints for external integrations; "
            "everything else requires a browser session."
        ),
        version="0.4.0",
        openapi_tags=[
            {
                "name": "insights",
                "description": "Public, API-key-authenticated read endpoints for external integrations.",
            }
        ],
        lifespan=_lifespan,
    )
    app.state.settings = settings
    # The Figma plugin runs in a sandboxed iframe with a `null` origin, so every
    # fetch it makes is cross-origin from this API's perspective; since it sends
    # a non-simple `X-API-Key` header, the browser preflights with `OPTIONS`.
    # `allow_origins=["*"]` + `allow_credentials=False` is deliberate: it opens
    # up the API-key-authenticated routes to any origin (which is what a public
    # API surface like this one, and the plugin, need) while browsers refuse to
    # honor `*` together with credentials -- so the web app's cookie-based
    # session auth remains unreachable cross-origin, i.e. unweakened by this.
    # Starlette wraps middleware in reverse-add order (last added = outermost),
    # so CORS must be added *after* the rate limiter -- otherwise a 429 raised
    # by SlowAPIMiddleware never passes back out through CORSMiddleware and
    # ships with no CORS headers, surfacing as an opaque network error instead
    # of a readable rate-limit response for cross-origin callers like the
    # Figma plugin.
    configure_rate_limiting(app, settings.redis_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
        allow_credentials=False,
    )
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.graph_sink = Neo4jGraphSink(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
    )
    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(personas_router)
    app.include_router(simulations_router)
    app.include_router(scheduled_simulations_router)
    app.include_router(events_router)
    app.include_router(graph_router)
    app.include_router(calibration_router)
    app.include_router(alerts_router)
    app.include_router(exports_router)
    app.include_router(settings_router)
    app.include_router(workspaces_router)
    app.include_router(integrations_router)
    app.include_router(onboarding_router)
    app.include_router(insights_router)
    app.include_router(billing_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
