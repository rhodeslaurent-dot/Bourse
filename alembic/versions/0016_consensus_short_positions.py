"""docs/23 — analyst consensus per source (S4, TradingView) and net short positions (S2).

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyst_consensus",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "day", "source", name="uq_analyst_consensus_isin_day_source"),
    )
    op.create_table(
        "short_positions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("authority", sa.String(16), nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("issuer", sa.String(160), nullable=True),
        sa.Column("holder", sa.String(200), nullable=False),
        sa.Column("pct", sa.Float(), nullable=False),
        sa.Column("position_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("authority", "isin", "holder", "position_date", name="uq_short_positions_holder_day"),
    )


def downgrade() -> None:
    op.drop_table("short_positions")
    op.drop_table("analyst_consensus")
