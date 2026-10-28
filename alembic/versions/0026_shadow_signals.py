"""docs/33 §33.3 — shadow signals of registration n° 2 on the European venues (stored, never shown).

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_signals",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("plan_id", sa.String(32), nullable=False),
        sa.Column("cell", sa.String(64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("venue_group", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("plan_id", "cell", "day", "isin", name="uq_shadow_signals"),
    )
    op.create_index("ix_shadow_signals_cell", "shadow_signals", ["cell"])
    op.create_index("ix_shadow_signals_day", "shadow_signals", ["day"])
    op.create_index("ix_shadow_signals_isin", "shadow_signals", ["isin"])


def downgrade() -> None:
    op.drop_table("shadow_signals")
