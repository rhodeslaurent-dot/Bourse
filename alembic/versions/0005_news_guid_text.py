"""news_items.guid as Text: RSS guids are often long URLs (> 255 chars) and Postgres refuses them.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("news_items") as batch:
        batch.alter_column("guid", existing_type=sa.String(255), type_=sa.Text(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("news_items") as batch:
        batch.alter_column("guid", existing_type=sa.Text(), type_=sa.String(255), existing_nullable=False)
