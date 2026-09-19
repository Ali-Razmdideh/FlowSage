from __future__ import annotations

import importlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from flowsage_backend.crypto import decrypt, derive_fernet_key, encrypt

OLD = "old-encryption-passphrase-for-tests"
NEW = "new-encryption-passphrase-for-tests"


async def test_rotation_dry_run_rerun_and_atomic_rollback(postgres_url: str) -> None:
    # Separate schema keeps this full-table maintenance command isolated from
    # other test data; real PostgreSQL transactions exercise rollback semantics.
    schema = "rotation_" + uuid.uuid4().hex
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = create_async_engine(
        postgres_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with isolated.begin() as conn:
            for table, column in [("jira_integrations", "api_token"), ("webhooks", "secret")]:
                await conn.execute(
                    text(f"CREATE TABLE {table} (id integer PRIMARY KEY, {column} text NOT NULL)")
                )
                await conn.execute(
                    text(f"INSERT INTO {table} VALUES (1, :value)"),
                    {"value": encrypt("credential", derive_fernet_key(OLD))},
                )
        module = importlib.util.find_spec("flowsage_backend.key_rotation")
        assert module is not None, "transactional encryption key rotation is missing"
        rotate = importlib.import_module("flowsage_backend.key_rotation").rotate_encryption_key
        assert await rotate(isolated, OLD, NEW, dry_run=True) == 2
        async with isolated.connect() as conn:
            raw = await conn.scalar(text("SELECT api_token FROM jira_integrations"))
            assert decrypt(raw, derive_fernet_key(OLD)) == "credential"
        # Failure in the second table must undo the first table's update.
        async with isolated.begin() as conn:
            await conn.execute(text("UPDATE webhooks SET secret = 'invalid'"))
        with pytest.raises(ValueError, match="cannot decrypt"):
            await rotate(isolated, OLD, NEW)
        async with isolated.begin() as conn:
            raw = await conn.scalar(text("SELECT api_token FROM jira_integrations"))
            assert decrypt(raw, derive_fernet_key(OLD)) == "credential"
            await conn.execute(
                text("UPDATE webhooks SET secret = :value"),
                {"value": encrypt("credential", derive_fernet_key(OLD))},
            )
        assert await rotate(isolated, OLD, NEW) == 2
        assert await rotate(isolated, OLD, NEW) == 0
        async with isolated.connect() as conn:
            for table, column in [("jira_integrations", "api_token"), ("webhooks", "secret")]:
                raw = await conn.scalar(text(f"SELECT {column} FROM {table}"))
                assert decrypt(raw, derive_fernet_key(NEW)) == "credential"
    finally:
        await isolated.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def test_cli_configuration_failure_does_not_print_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flowsage_backend.config import get_settings
    from flowsage_backend.key_rotation import run_key_rotation

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "short-private-jwt")
    monkeypatch.setenv("OLD_SECRET_ENCRYPTION_KEY", OLD)
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", NEW)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as error:
            await run_key_rotation(dry_run=True)
        assert "short-private-jwt" not in str(error.value)
        assert OLD not in str(error.value)
        assert NEW not in str(error.value)
    finally:
        get_settings.cache_clear()


def test_rotation_cli_dispatches_without_accepting_key_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flowsage_backend.__main__ import main

    monkeypatch.setattr("sys.argv", ["flowsage-backend", "rotate-encryption-key", "--dry-run"])
    monkeypatch.delenv("OLD_SECRET_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
    with pytest.raises(SystemExit, match="Set OLD_SECRET_ENCRYPTION_KEY"):
        main()
