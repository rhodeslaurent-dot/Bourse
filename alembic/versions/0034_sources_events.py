"""Step 1 of the user's plan (15/11/2026): source health per day (S1), one journal of market events (S2), their
measure against the market (S3) and FINRA daily short sale volume for US values (S5). New tables only.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "source_health",
        _id(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("items_day", sa.Integer(), nullable=False),
        sa.Column("items_7d", sa.Integer(), nullable=False),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.Column("first_day", sa.Date(), nullable=True),
        sa.Column("last_day", sa.Date(), nullable=True),
        sa.Column("silent_weekdays", sa.Integer(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("day", "source", name="uq_source_health_day_source"),
    )
    op.create_index("ix_source_health_day", "source_health", ["day"])
    op.create_table(
        "market_events",
        _id(),
        sa.Column("source_key", sa.String(48), nullable=False),
        sa.Column("family", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("mic", sa.String(8), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.Integer(), nullable=True),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("ref_table", sa.String(32), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ref_table", "ref_id", "kind", "isin", name="uq_market_events_ref"),
    )
    for col in ("source_key", "kind", "isin", "event_at", "created_at"):
        op.create_index(f"ix_market_events_{col}", "market_events", [col])
    op.create_table(
        "event_outcomes",
        _id(),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("ref_day", sa.Date(), nullable=True),
        sa.Column("end_day", sa.Date(), nullable=True),
        sa.Column("ref_close", sa.Float(), nullable=True),
        sa.Column("end_close", sa.Float(), nullable=True),
        sa.Column("ret", sa.Float(), nullable=True),
        sa.Column("venue_median", sa.Float(), nullable=True),
        sa.Column("excess_venue", sa.Float(), nullable=True),
        sa.Column("sector_median", sa.Float(), nullable=True),
        sa.Column("excess_sector", sa.Float(), nullable=True),
        sa.Column("note", sa.String(160), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "horizon", name="uq_event_outcomes_event_horizon"),
    )
    op.create_index("ix_event_outcomes_event_id", "event_outcomes", ["event_id"])
    op.create_table(
        "short_volume_daily",
        _id(),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("short_volume", sa.Float(), nullable=False),
        sa.Column("short_exempt_volume", sa.Float(), nullable=True),
        sa.Column("total_volume", sa.Float(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "day", "source", name="uq_short_volume_daily"),
    )
    op.create_index("ix_short_volume_daily_isin", "short_volume_daily", ["isin"])
    op.create_index("ix_short_volume_daily_day", "short_volume_daily", ["day"])


def downgrade() -> None:
    op.drop_table("short_volume_daily")
    op.drop_table("event_outcomes")
    op.drop_table("market_events")
    op.drop_table("source_health")
