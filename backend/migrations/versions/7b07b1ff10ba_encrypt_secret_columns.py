"""encrypt secret columns

Revision ID: 7b07b1ff10ba
Revises: 3769700b545d
Create Date: 2026-07-24 10:55:01.948124

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b07b1ff10ba'
down_revision: Union[str, Sequence[str], None] = '3769700b545d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen api_token/secret to fit Fernet ciphertext; existing rows in this
    fresh dev stack have no prior data to re-encrypt (no production deployment
    exists yet)."""
    op.alter_column("jira_integrations", "api_token", type_=sa.String(length=1000))
    op.alter_column("webhooks", "secret", type_=sa.String(length=500))


def downgrade() -> None:
    op.alter_column("webhooks", "secret", type_=sa.String(length=64))
    op.alter_column("jira_integrations", "api_token", type_=sa.String(length=500))
