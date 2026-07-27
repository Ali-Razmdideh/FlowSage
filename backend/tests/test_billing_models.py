import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.billing import (
    SubscriptionStatus,
    SubscriptionTier,
    WorkspaceSubscription,
)
from flowsage_backend.models.workspace import Workspace


async def test_workspace_subscription_defaults(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Sub Test", slug=f"sub-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()

    subscription = WorkspaceSubscription(workspace_id=workspace.id)
    db_session.add(subscription)
    await db_session.commit()
    await db_session.refresh(subscription)

    assert subscription.tier == SubscriptionTier.FREE
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.stripe_customer_id is None
    assert subscription.stripe_subscription_id is None
    assert subscription.current_period_end is None
