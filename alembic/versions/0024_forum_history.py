"""docs/26 — progress of the multi-night forum history reading (user decision of 26/10/2026).

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forum_history_progress",
        sa.Column("isin", sa.String(12), primary_key=True),
        sa.Column("next_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("oldest_activity", sa.Date(), nullable=True),
        sa.Column("cutoff", sa.Date(), nullable=True),
        sa.Column("seen", sa.JSON(), nullable=True),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("forum_history_progress")
