"""backfill default workspace

Revision ID: e463496b1d0f
Revises: 624fdeacb601
Create Date: 2026-07-19 22:55:37.087937

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e463496b1d0f'
down_revision: Union[str, Sequence[str], None] = '624fdeacb601'
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
    bind = op.get_bind()
    workspace_id = uuid.uuid4()

    # `privacy`/`role` below are bound as their native Postgres enum types (rather than
    # plain sa.String()) -- asyncpg (unlike psycopg2) requires the bind parameter's type
    # to match the column's type exactly, or the INSERT fails with "column is of type
    # workspace_privacy but expression is of type character varying".
    workspaces = sa.table(
        'workspaces',
        sa.column('id', sa.Uuid()),
        sa.column('name', sa.String()),
        sa.column('slug', sa.String()),
        sa.column('description', sa.Text()),
        sa.column('privacy', sa.Enum('PRIVATE', 'RESTRICTED', name='workspace_privacy', create_type=False)),
        sa.column('region', sa.String()),
        sa.column('retention_days', sa.Integer()),
        sa.column('archived', sa.Boolean()),
    )
    bind.execute(
        workspaces.insert().values(
            id=workspace_id,
            name="Default",
            slug="fs-default",
            description="",
            privacy="PRIVATE",
            region="us",
            retention_days=90,
            archived=False,
        )
    )

    memberships = sa.table(
        'memberships',
        sa.column('id', sa.Uuid()),
        sa.column('user_id', sa.Uuid()),
        sa.column('workspace_id', sa.Uuid()),
        sa.column('role', sa.Enum('VIEWER', 'RESEARCHER', 'ADMIN', name='membership_role', create_type=False)),
    )
    users = sa.table('users', sa.column('id', sa.Uuid()))
    for (user_id,) in bind.execute(sa.select(users.c.id)).fetchall():
        bind.execute(
            memberships.insert().values(
                id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, role="ADMIN"
            )
        )

    for table_name in _TENANT_TABLES:
        table = sa.table(table_name, sa.column('workspace_id', sa.Uuid()))
        bind.execute(table.update().values(workspace_id=workspace_id))
        op.alter_column(table_name, 'workspace_id', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in _TENANT_TABLES:
        op.alter_column(table_name, 'workspace_id', nullable=True)
    op.execute("DELETE FROM memberships")
    op.execute("DELETE FROM workspaces")
