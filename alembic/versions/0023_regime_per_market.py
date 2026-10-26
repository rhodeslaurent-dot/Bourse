"""docs/32 — market regime per venue: a ``market`` column (MIC, or EU for the European reference). Rows
written before were the single CAC 40 + STOXX 600 reading: they become the European row.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("market_regime", sa.Column("market", sa.String(8), nullable=False, server_default="EU"))
    op.create_index("ix_market_regime_market", "market_regime", ["market"])


def downgrade() -> None:
    op.drop_index("ix_market_regime_market", table_name="market_regime")
    op.drop_column("market_regime", "market")
