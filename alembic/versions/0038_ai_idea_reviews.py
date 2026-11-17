"""Step 2, option A (17/11/2026): three authors of levels on the day's ideas (Python, AI analyst, AI contradictor)
and an AI arbiter, in observation. New table.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_idea_reviews",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("dossier_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("python", sa.JSON(), nullable=True),
        sa.Column("analyst", sa.JSON(), nullable=True),
        sa.Column("contradictor", sa.JSON(), nullable=True),
        sa.Column("arbiter", sa.JSON(), nullable=True),
        sa.Column("chosen", sa.String(16), nullable=True),
        sa.Column("dossier", sa.JSON(), nullable=True),
        sa.Column("rejected", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("models", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_idea_reviews_day", "ai_idea_reviews", ["day"])
    op.create_index("ix_ai_idea_reviews_isin", "ai_idea_reviews", ["isin"])


def downgrade() -> None:
    op.drop_table("ai_idea_reviews")
