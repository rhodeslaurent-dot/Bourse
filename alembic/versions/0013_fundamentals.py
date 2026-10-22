"""Free fundamentals and analyst consensus (phase 3, docs/22 §22.7) — one row per instrument and day.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fundamentals",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("isin", "day", name="uq_fundamentals_isin_day"),
    )


def downgrade() -> None:
    op.drop_table("fundamentals")
