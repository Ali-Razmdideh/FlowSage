import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.insight_cache import compute_input_hash, get_cached, is_fresh, upsert_cached
from flowsage_backend.models.workspace import Workspace


async def _workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Insight Cache Test", slug=f"insight-cache-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


def test_compute_input_hash_is_deterministic_and_order_independent() -> None:
    a = compute_input_hash({"x": 1, "y": 2})
    b = compute_input_hash({"y": 2, "x": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex digest


def test_compute_input_hash_differs_for_different_signals() -> None:
    assert compute_input_hash({"x": 1}) != compute_input_hash({"x": 2})


async def test_get_cached_returns_none_when_absent(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    assert await get_cached(db_session, workspace_id, "node_intelligence", "checkout") is None


async def test_upsert_then_get_cached_round_trips(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    await upsert_cached(
        db_session,
        workspace_id,
        "node_intelligence",
        "checkout",
        "hash1",
        {"insight": "text", "recommendations": []},
        "claude-haiku-4-5-20251001",
    )

    cached = await get_cached(db_session, workspace_id, "node_intelligence", "checkout")
    assert cached is not None
    assert cached.input_hash == "hash1"
    assert cached.payload == {"insight": "text", "recommendations": []}
    assert cached.model == "claude-haiku-4-5-20251001"


async def test_upsert_cached_replaces_stale_row_on_conflict(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    await upsert_cached(
        db_session, workspace_id, "node_intelligence", "checkout", "hash1", {"a": 1}, "m1"
    )
    await upsert_cached(
        db_session, workspace_id, "node_intelligence", "checkout", "hash2", {"a": 2}, "m2"
    )

    cached = await get_cached(db_session, workspace_id, "node_intelligence", "checkout")
    assert cached is not None
    assert cached.input_hash == "hash2"
    assert cached.payload == {"a": 2}


async def test_is_fresh_true_only_when_hash_matches(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    assert (
        await is_fresh(db_session, workspace_id, "node_intelligence", "checkout", "hash1") is False
    )

    await upsert_cached(
        db_session, workspace_id, "node_intelligence", "checkout", "hash1", {"a": 1}, "m1"
    )
    assert (
        await is_fresh(db_session, workspace_id, "node_intelligence", "checkout", "hash1") is True
    )
    assert (
        await is_fresh(db_session, workspace_id, "node_intelligence", "checkout", "stale") is False
    )
