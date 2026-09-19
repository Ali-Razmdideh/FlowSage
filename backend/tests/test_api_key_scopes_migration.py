"""Exercise legacy-key compatibility separately from new-key defaults."""

import asyncio
import logging
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from flowsage_backend.config import get_settings
from flowsage_backend.models.api_key import ALL_API_KEY_SCOPES


def test_existing_keys_backfilled_new_keys_default_to_ingestion(monkeypatch):
    # Alembic loads alembic.ini's logging config in-process. Preserve each
    # logger's disabled state so this migration test cannot suppress logging
    # captured by unrelated tests that run later in the same pytest process.
    logger_states = {
        name: logger.disabled
        for name, logger in logging.root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    with PostgresContainer("postgres:16-alpine") as container:
        url = container.get_connection_url().replace("psycopg2", "asyncpg")
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        backend = Path(__file__).resolve().parent.parent
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "migrations"))
        try:
            command.upgrade(config, "0ba4cbaefde6")

            async def insert_key(name):
                engine = create_async_engine(url)
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO api_keys (id, workspace_id, name, key_prefix, key_hash) SELECT :id, id, :name, 'prefix', :hash FROM workspaces WHERE slug='fs-default'"
                        ),
                        {"id": uuid.uuid4(), "name": name, "hash": name},
                    )
                await engine.dispose()

            asyncio.run(insert_key("legacy"))
            command.upgrade(config, "head")
            asyncio.run(insert_key("fresh"))

            async def read_keys():
                engine = create_async_engine(url)
                async with engine.connect() as connection:
                    rows = (
                        await connection.execute(text("SELECT name, scopes FROM api_keys"))
                    ).all()
                await engine.dispose()
                return dict(rows)

            keys = asyncio.run(read_keys())
            assert set(keys["legacy"]) == set(ALL_API_KEY_SCOPES)
            assert keys["fresh"] == ["events:write"]
        finally:
            get_settings.cache_clear()
            for name, disabled in logger_states.items():
                logging.getLogger(name).disabled = disabled
