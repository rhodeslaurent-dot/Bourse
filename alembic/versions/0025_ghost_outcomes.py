"""docs/33 — ghost of never-filled proposals (user decision of 27/10/2026): stored, never shown.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("ghost_ref_price", "ghost_ret_pct", "ghost_gap_pct"):
        op.add_column("signal_outcomes", sa.Column(col, sa.Float(), nullable=True))


def downgrade() -> None:
    for col in ("ghost_gap_pct", "ghost_ret_pct", "ghost_ref_price"):
        op.drop_column("signal_outcomes", col)
