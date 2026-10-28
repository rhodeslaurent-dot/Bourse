"""docs/34 §34.4 — history of earnings announcements (dates, time of day, reported and estimated EPS).

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "earnings_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("when", sa.String(10), nullable=False),
        sa.Column("eps_actual", sa.Float(), nullable=True),
        sa.Column("eps_estimate", sa.Float(), nullable=True),
        sa.Column("surprise_pct", sa.Float(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "day", "source", name="uq_earnings_events_isin_day_source"),
    )
    op.create_index("ix_earnings_events_isin", "earnings_events", ["isin"])
    op.create_index("ix_earnings_events_day", "earnings_events", ["day"])


def downgrade() -> None:
    op.drop_table("earnings_events")
