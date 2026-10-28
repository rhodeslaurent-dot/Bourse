"""Intraday re-evaluation of open proposals with fresh quotes (user request of 28/10/2026).

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposal_reevaluations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("market_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_status", sa.String(12), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("actionable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reasons", sa.JSON(), nullable=True),
    )
    op.create_index("ix_proposal_reevaluations_proposal_id", "proposal_reevaluations", ["proposal_id"])
    op.create_index("ix_proposal_reevaluations_ts", "proposal_reevaluations", ["ts"])


def downgrade() -> None:
    op.drop_table("proposal_reevaluations")
