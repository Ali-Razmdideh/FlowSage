"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from flowsage_backend.api.auth import router as auth_router
from flowsage_backend.config import Settings, get_settings
from flowsage_backend.db import create_engine, create_session_factory


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="FlowSage API", lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.include_router(auth_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        async with app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
