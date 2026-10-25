"""docs/30 S4a — aggregates of the forum messages of a value (never a text or an author).

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "social_content",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("window_hours", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("summaries", sa.JSON(), nullable=True),
        sa.Column("effect", sa.String(16), nullable=True),
        sa.Column("threads", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(300), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("social_content")
