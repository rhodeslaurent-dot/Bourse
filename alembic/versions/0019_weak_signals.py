"""docs/26 — weak signals radar: Wikipedia articles per value, daily attention counts, radar labels.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "wiki_articles",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("lang", sa.String(8), nullable=False),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "lang", name="uq_wiki_articles_isin_lang"),
    )
    op.create_table(
        "attention_daily",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("lang", sa.String(8), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "day", "source", "lang", name="uq_attention_daily"),
    )
    op.create_table(
        "weak_signal_hits",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("idea", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attention_z", sa.Float(), nullable=True),
        sa.Column("rvol", sa.Float(), nullable=True),
        sa.Column("news_recent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("text", sa.String(300), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "day", name="uq_weak_signal_hits_isin_day"),
    )


def downgrade() -> None:
    op.drop_table("weak_signal_hits")
    op.drop_table("attention_daily")
    op.drop_table("wiki_articles")
