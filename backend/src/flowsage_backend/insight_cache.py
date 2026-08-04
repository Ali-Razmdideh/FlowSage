"""Generic on-demand cache for Claude-generated narrative text (see
flowsage_predict.narrative), keyed by an input hash so a GET only pays for a
fresh Claude call when the underlying signal has actually changed since the
last successful generation. Written to exclusively by the arq job functions
in worker.py and retraining.execute_retraining -- read from synchronously by
churn.py/calibration.py, which never call Claude themselves.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.generated_insight import GeneratedInsight


def compute_input_hash(signal: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(signal, sort_keys=True).encode()).hexdigest()


async def get_cached(
    session: AsyncSession, workspace_id: uuid.UUID, kind: str, cache_key: str
) -> GeneratedInsight | None:
    result = await session.execute(
        select(GeneratedInsight).where(
            GeneratedInsight.workspace_id == workspace_id,
            GeneratedInsight.kind == kind,
            GeneratedInsight.cache_key == cache_key,
        )
    )
    return result.scalar_one_or_none()


async def is_fresh(
    session: AsyncSession, workspace_id: uuid.UUID, kind: str, cache_key: str, input_hash: str
) -> bool:
    cached = await get_cached(session, workspace_id, kind, cache_key)
    return cached is not None and cached.input_hash == input_hash


async def upsert_cached(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    kind: str,
    cache_key: str,
    input_hash: str,
    payload: dict[str, object],
    model: str,
) -> None:
    """Insert-or-replace on the (workspace_id, kind, cache_key) unique
    constraint -- an upsert rather than a plain insert because a screen's
    second-ever generation (after the underlying signal changes again) must
    replace the stale row, not violate the unique constraint."""
    stmt = pg_insert(GeneratedInsight).values(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind=kind,
        cache_key=cache_key,
        input_hash=input_hash,
        payload=payload,
        model=model,
        created_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["workspace_id", "kind", "cache_key"],
        set_={
            "input_hash": stmt.excluded.input_hash,
            "payload": stmt.excluded.payload,
            "model": stmt.excluded.model,
            "created_at": stmt.excluded.created_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
