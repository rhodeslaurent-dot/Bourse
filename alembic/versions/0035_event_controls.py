"""Step 1 control group (16/11/2026): the same value over the same sessions, before and after each event. New table.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_controls",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("offset", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint("event_id", "horizon", "offset", name="uq_event_controls_event_horizon_offset"),
    )
    op.create_index("ix_event_controls_event_id", "event_controls", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_event_controls_event_id", table_name="event_controls")
    op.drop_table("event_controls")
