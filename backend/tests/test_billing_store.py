import uuid
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend import billing_store
from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import SubscriptionTier, WorkspaceSubscription
from flowsage_backend.models.workspace import Workspace


async def test_get_or_create_subscription_creates_free_tier_on_first_access(
    db_session: AsyncSession,
) -> None:
    workspace = Workspace(name="Store Test", slug=f"store-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    await db_session.commit()

    subscription = await get_or_create_subscription(db_session, workspace.id)

    assert subscription.workspace_id == workspace.id
    assert subscription.tier == SubscriptionTier.FREE


async def test_get_or_create_subscription_returns_existing_row(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Store Test 2", slug=f"store-test2-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    await db_session.commit()

    first = await get_or_create_subscription(db_session, workspace.id)
    first.tier = SubscriptionTier.PRO
    await db_session.commit()

    second = await get_or_create_subscription(db_session, workspace.id)
    assert second.id == first.id
    assert second.tier == SubscriptionTier.PRO


async def test_get_or_create_subscription_survives_concurrent_insert_race(
    db_session: AsyncSession,
) -> None:
    """Two concurrent first-touch requests both miss the initial SELECT and both
    try to INSERT; the unique index on `workspace_id` fails the loser. It must
    recover by re-reading the winner's row, not blow up with a 500 (this used to
    surface as an IntegrityError out of `POST /v1/events`).

    The race is reproduced deterministically by forcing the *first* SELECT to
    miss for a workspace whose row already exists -- exactly the state the
    losing request is in when it reaches the INSERT.
    """
    workspace = Workspace(name="Race Test", slug=f"race-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()

    winner = await get_or_create_subscription(db_session, workspace.id)
    db_session.expunge_all()

    real_select = billing_store._select_subscription
    calls = {"n": 0}

    async def _select_missing_first(
        session: AsyncSession, workspace_id: uuid.UUID
    ) -> WorkspaceSubscription | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_select(session, workspace_id)

    with patch.object(billing_store, "_select_subscription", _select_missing_first):
        loser = await get_or_create_subscription(db_session, workspace.id)

    assert calls["n"] == 2
    assert loser.id == winner.id
