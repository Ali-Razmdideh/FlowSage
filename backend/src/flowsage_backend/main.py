"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import arq
from arq.connections import RedisSettings
from fastapi import FastAPI
from flowsage_graph.ingest import Neo4jGraphSink
from sqlalchemy import text

from flowsage_backend.api.alerts import router as alerts_router
from flowsage_backend.api.audit import router as audit_router
from flowsage_backend.api.auth import router as auth_router
from flowsage_backend.api.calibration import router as calibration_router
from flowsage_backend.api.events import events_router, graph_router
from flowsage_backend.api.exports import router as exports_router
from flowsage_backend.api.integrations import router as integrations_router
from flowsage_backend.api.personas import router as personas_router
from flowsage_backend.api.settings import router as settings_router
from flowsage_backend.api.simulations import router as simulations_router
from flowsage_backend.api.workspaces import router as workspaces_router
from flowsage_backend.config import Settings, get_settings
from flowsage_backend.db import create_engine, create_session_factory


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.arq_pool = await arq.create_pool(RedisSettings.from_dsn(app.state.settings.redis_url))
    yield
    await app.state.arq_pool.aclose()
    await asyncio.to_thread(app.state.graph_sink.close)
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="FlowSage API", lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.graph_sink = Neo4jGraphSink(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
    )
    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(personas_router)
    app.include_router(simulations_router)
    app.include_router(events_router)
    app.include_router(graph_router)
    app.include_router(calibration_router)
    app.include_router(alerts_router)
    app.include_router(exports_router)
    app.include_router(settings_router)
    app.include_router(workspaces_router)
    app.include_router(integrations_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
