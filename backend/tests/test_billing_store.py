import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import SubscriptionTier
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
