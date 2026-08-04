"""add generated_insights table

Revision ID: 0ba4cbaefde6
Revises: 7c2f9a4d18be
Create Date: 2026-08-04 14:16:30.820411

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0ba4cbaefde6"
down_revision: Union[str, Sequence[str], None] = "7c2f9a4d18be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_insights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("cache_key", sa.String(length=200), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "kind", "cache_key", name="uq_generated_insight_key"
        ),
    )
    op.create_index(
        op.f("ix_generated_insights_workspace_id"), "generated_insights", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generated_insights_workspace_id"), table_name="generated_insights")
    op.drop_table("generated_insights")
    # No native Enum column on this table (kind is a plain String, deliberately --
    # see this plan's Global Constraints), so no enum-drop-on-downgrade step is
    # needed here, unlike run_status/schedule_interval.
