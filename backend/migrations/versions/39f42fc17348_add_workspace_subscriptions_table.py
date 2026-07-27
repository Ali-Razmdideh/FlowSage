"""add workspace_subscriptions table

Revision ID: 39f42fc17348
Revises: 4afeed2e2a8c
Create Date: 2026-07-27 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "39f42fc17348"
down_revision: Union[str, Sequence[str], None] = "4afeed2e2a8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tier", sa.Enum("FREE", "PRO", "TEAM", name="subscription_tier"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "PAST_DUE", "CANCELED", name="subscription_status"),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_index(
        op.f("ix_workspace_subscriptions_workspace_id"),
        "workspace_subscriptions",
        ["workspace_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_workspace_subscriptions_workspace_id"), table_name="workspace_subscriptions"
    )
    op.drop_table("workspace_subscriptions")
    # Postgres native Enum types survive table drop; must drop explicitly, or a
    # down-then-up cycle fails with "type subscription_tier already exists".
    sa.Enum(name="subscription_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="subscription_tier").drop(op.get_bind(), checkfirst=True)
