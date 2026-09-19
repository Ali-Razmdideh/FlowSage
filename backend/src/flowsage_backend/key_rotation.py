"""Explicit, transactional maintenance for encrypted integration credentials."""

from __future__ import annotations

import os

from cryptography.fernet import InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from flowsage_backend.config import _PLACEHOLDER_ENCRYPTION_KEY, get_settings
from flowsage_backend.crypto import decrypt, derive_fernet_key, encrypt
from flowsage_backend.db import create_engine


async def rotate_encryption_key(
    engine: AsyncEngine, old_secret: str, new_secret: str, *, dry_run: bool = False
) -> int:
    """Rotate raw columns without ORM encryption hooks; return rows needing rotation.

    Call with API and workers stopped. The table locks also prevent concurrent
    writers during the transaction. Dry runs decrypt everything but write nothing.
    """
    if not old_secret:
        raise ValueError("OLD_SECRET_ENCRYPTION_KEY must be set.")
    if new_secret == _PLACEHOLDER_ENCRYPTION_KEY or len(new_secret.strip().encode("utf-8")) < 32:
        raise ValueError("SECRET_ENCRYPTION_KEY must be a non-placeholder of at least 32 bytes.")
    old_key, new_key = derive_fernet_key(old_secret), derive_fernet_key(new_secret)
    changed = 0
    async with engine.begin() as connection:
        await connection.execute(text("LOCK TABLE jira_integrations, webhooks IN EXCLUSIVE MODE"))
        for table, column in (("jira_integrations", "api_token"), ("webhooks", "secret")):
            rows = await connection.execute(text(f"SELECT id, {column} FROM {table}"))
            for row_id, ciphertext in rows:
                try:
                    decrypt(ciphertext, new_key)
                    continue
                except (InvalidToken, UnicodeError, ValueError):
                    pass
                try:
                    plaintext = decrypt(ciphertext, old_key)
                except (InvalidToken, UnicodeError, ValueError):
                    raise ValueError(
                        f"Key rotation cannot decrypt a row in {table}; no changes committed."
                    ) from None
                changed += 1
                if not dry_run:
                    await connection.execute(
                        text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
                        {"value": encrypt(plaintext, new_key), "id": row_id},
                    )
    return changed


async def run_key_rotation(*, dry_run: bool = False) -> None:
    """Read keys from the process environment, never command arguments or logs."""
    old_secret = os.environ.get("OLD_SECRET_ENCRYPTION_KEY", "")
    new_secret = os.environ.get("SECRET_ENCRYPTION_KEY", "")
    if not old_secret or not new_secret:
        raise SystemExit("Set OLD_SECRET_ENCRYPTION_KEY and SECRET_ENCRYPTION_KEY.")
    try:
        settings = get_settings()
    except ValueError:
        raise SystemExit(
            "Invalid application configuration; check environment and secret settings."
        ) from None
    engine = create_engine(settings)
    try:
        count = await rotate_encryption_key(engine, old_secret, new_secret, dry_run=dry_run)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        await engine.dispose()
    action = "Would rotate" if dry_run else "Rotated"
    print(f"{action} {count} encrypted credential(s).")
