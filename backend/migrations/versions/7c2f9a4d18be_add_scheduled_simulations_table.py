"""add scheduled_simulations table

Revision ID: 7c2f9a4d18be
Revises: 39f42fc17348
Create Date: 2026-08-01 18:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c2f9a4d18be"
down_revision: Union[str, Sequence[str], None] = "39f42fc17348"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_simulations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("flow_name", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.String(length=500), nullable=False),
        sa.Column("persona_id", sa.Uuid(), nullable=False),
        sa.Column(
            "interval",
            sa.Enum("DAILY", "WEEKLY", "ON_PUSH", name="schedule_interval"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("pending_screenshots_dir", sa.String(length=1000), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scheduled_simulations_workspace_id"),
        "scheduled_simulations",
        ["workspace_id"],
    )
    op.add_column("simulation_runs", sa.Column("scheduled_simulation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_simulation_runs_scheduled_simulation_id",
        "simulation_runs",
        "scheduled_simulations",
        ["scheduled_simulation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_simulation_runs_scheduled_simulation_id"),
        "simulation_runs",
        ["scheduled_simulation_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_simulation_runs_scheduled_simulation_id"), table_name="simulation_runs")
    op.drop_constraint(
        "fk_simulation_runs_scheduled_simulation_id", "simulation_runs", type_="foreignkey"
    )
    op.drop_column("simulation_runs", "scheduled_simulation_id")
    op.drop_index(op.f("ix_scheduled_simulations_workspace_id"), table_name="scheduled_simulations")
    op.drop_table("scheduled_simulations")
    # Postgres native Enum types survive table drop; must drop explicitly, or a
    # down-then-up cycle fails with "type schedule_interval already exists" (same
    # fix as run_status/digest_frequency in earlier migrations).
    sa.Enum(name="schedule_interval").drop(op.get_bind(), checkfirst=True)
