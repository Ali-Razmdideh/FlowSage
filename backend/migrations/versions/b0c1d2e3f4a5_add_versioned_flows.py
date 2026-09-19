"""add versioned flows

Revision ID: b0c1d2e3f4a5
Revises: 8d51a7e9b203
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "8d51a7e9b203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "key", name="uq_flow_workspace_key"),
    )
    op.create_index("ix_flows_workspace_id", "flows", ["workspace_id"])
    op.create_table(
        "flow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("flow_id", sa.Uuid(), sa.ForeignKey("flows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("flow_id", "version", name="uq_flow_version"),
    )
    op.create_index("ix_flow_versions_flow_id", "flow_versions", ["flow_id"])
    for table in ("events", "simulation_runs"):
        op.add_column(table, sa.Column("flow_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("flow_version", sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_{table}_flow_id", table, "flows", ["flow_id"], ["id"], ondelete="SET NULL")
        op.create_index(f"ix_{table}_flow_id", table, ["flow_id"])


def downgrade() -> None:
    for table in ("simulation_runs", "events"):
        op.drop_index(f"ix_{table}_flow_id", table_name=table)
        op.drop_constraint(f"fk_{table}_flow_id", table, type_="foreignkey")
        op.drop_column(table, "flow_version")
        op.drop_column(table, "flow_id")
    op.drop_table("flow_versions")
    op.drop_table("flows")
