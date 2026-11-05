"""Lot C1 (docs/39 §5, R17) — the core pocket's share of the consolidated equity snapshot: the circuit breaker
measures the active part only (a fall of the world index on the core never blocks the active part's entries).
Rows written before have NULL (no core held then: counted as 0).

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("kpi_daily") as b:
        b.add_column(sa.Column("core_latent_pnl", sa.Float(), nullable=True))
        b.add_column(sa.Column("core_realized_pnl_cum", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("kpi_daily") as b:
        b.drop_column("core_realized_pnl_cum")
        b.drop_column("core_latent_pnl")
