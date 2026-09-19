"""Add explicit API-key scopes, retaining existing key capabilities.

Revision ID: 8d51a7e9b203
Revises: 0ba4cbaefde6
"""

from alembic import op
import sqlalchemy as sa

revision = "8d51a7e9b203"
down_revision = "0ba4cbaefde6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing keys retain all capabilities; subsequent inserts get least privilege.
    op.add_column("api_keys", sa.Column("scopes", sa.JSON(), nullable=True))
    op.execute(
        sa.text(
            """UPDATE api_keys SET scopes = '["events:write", "insights:read", "personas:read", "simulations:read", "simulations:write", "schedules:read", "schedules:write"]' """
        )
    )
    op.alter_column("api_keys", "scopes", nullable=False, server_default='["events:write"]')


def downgrade() -> None:
    op.drop_column("api_keys", "scopes")
