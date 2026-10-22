"""Portfolio review and daily report (lot 2.5, docs/22 §22.5, docs/09 §9.2) — portfolio_reviews,
daily_reports (the 10-rubric report of the day, archived as built).

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "portfolio_reviews",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("lines", sa.JSON, nullable=True),
        sa.Column("synthesis", sa.JSON, nullable=True),
    )
    op.create_table(
        "daily_reports",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, unique=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("daily_reports")
    op.drop_table("portfolio_reviews")
