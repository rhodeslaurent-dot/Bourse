"""18/11/2026 — the circuit breaker measures gains and losses (realised + latent), which do not depend on cash; an
account whose cash was never declared (a declarative PEA) marked every equity snapshot incomplete and so left the
measure missing for weeks (every new entry blocked, decision of 04/11/2026). ``pnl_incomplete``: a line without price
or exchange rate (the P&L itself partial), apart from ``incomplete`` (equity partial). Rows written before: NULL
(read as ``incomplete``, never assumed complete).

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("kpi_daily") as b:
        b.add_column(sa.Column("pnl_incomplete", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("kpi_daily") as b:
        b.drop_column("pnl_incomplete")
