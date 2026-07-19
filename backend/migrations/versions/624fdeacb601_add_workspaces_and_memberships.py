"""add workspaces and memberships

Revision ID: 624fdeacb601
Revises: 1c165b4afcfa
Create Date: 2026-07-19 22:55:28.328149

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "624fdeacb601"
down_revision: Union[str, Sequence[str], None] = "1c165b4afcfa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT_TABLES = [
    "personas",
    "persona_memories",
    "simulation_runs",
    "simulation_steps",
    "friction_issues",
    "retraining_jobs",
    "calibration_settings",
    "events",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column(
            "privacy",
            sa.Enum("PRIVATE", "RESTRICTED", name="workspace_privacy"),
            nullable=False,
        ),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workspaces_slug"), "workspaces", ["slug"], unique=True)

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("VIEWER", "RESEARCHER", "ADMIN", name="membership_role"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),
    )
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_memberships_workspace_id"), "memberships", ["workspace_id"], unique=False
    )

    for table in _TENANT_TABLES:
        op.add_column(table, sa.Column("workspace_id", sa.Uuid(), nullable=True))
        op.create_index(op.f(f"ix_{table}_workspace_id"), table, ["workspace_id"], unique=False)
        op.create_foreign_key(
            f"fk_{table}_workspace_id",
            table,
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # personas.slug was globally unique; now that personas are workspace-scoped, two
    # different workspaces must each be able to seed the same 5 baseline persona slugs.
    op.drop_index(op.f("ix_personas_slug"), table_name="personas")
    op.create_unique_constraint("uq_persona_slug_workspace", "personas", ["slug", "workspace_id"])
    op.create_index(op.f("ix_personas_slug"), "personas", ["slug"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_personas_slug"), table_name="personas")
    op.drop_constraint("uq_persona_slug_workspace", "personas", type_="unique")
    op.create_index(op.f("ix_personas_slug"), "personas", ["slug"], unique=True)

    for table in reversed(_TENANT_TABLES):
        op.drop_constraint(f"fk_{table}_workspace_id", table, type_="foreignkey")
        op.drop_index(op.f(f"ix_{table}_workspace_id"), table_name=table)
        op.drop_column(table, "workspace_id")

    op.drop_index(op.f("ix_memberships_workspace_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_table("memberships")
    # Postgres native Enum types survive table drop; must drop explicitly, or a
    # down-then-up cycle fails with "type membership_role already exists" (same
    # fix as run_status/retraining_status/digest_frequency in earlier migrations).
    sa.Enum(name="membership_role").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_workspaces_slug"), table_name="workspaces")
    op.drop_table("workspaces")
    sa.Enum(name="workspace_privacy").drop(op.get_bind(), checkfirst=True)
