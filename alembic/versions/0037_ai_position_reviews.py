"""Step 2, option B (17/11/2026): the evening AI review of each held position, in observation. New table.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_position_reviews",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("dossier_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("action", sa.String(16), nullable=True),
        sa.Column("stop_ai", sa.Float(), nullable=True),
        sa.Column("part_ai", sa.Float(), nullable=True),
        sa.Column("shares_ai", sa.Integer(), nullable=True),
        sa.Column("ref_close", sa.Float(), nullable=True),
        sa.Column("python", sa.JSON(), nullable=True),
        sa.Column("dossier", sa.JSON(), nullable=True),
        sa.Column("analyst", sa.JSON(), nullable=True),
        sa.Column("contradictor", sa.JSON(), nullable=True),
        sa.Column("rejected", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("models", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("position_id", "day", name="uq_ai_position_reviews_position_day"),
    )
    op.create_index("ix_ai_position_reviews_day", "ai_position_reviews", ["day"])
    op.create_index("ix_ai_position_reviews_position_id", "ai_position_reviews", ["position_id"])
    op.create_index("ix_ai_position_reviews_isin", "ai_position_reviews", ["isin"])


def downgrade() -> None:
    op.drop_table("ai_position_reviews")
