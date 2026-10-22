"""Detector names longer than 16 characters (d3_range_expansion) — widen signals/trades.detector.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("signals") as b:
        b.alter_column("detector", type_=sa.String(32), existing_type=sa.String(16), existing_nullable=False)
    with op.batch_alter_table("trades") as b:
        b.alter_column("detector", type_=sa.String(32), existing_type=sa.String(16), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("trades") as b:
        b.alter_column("detector", type_=sa.String(16), existing_type=sa.String(32), existing_nullable=True)
    with op.batch_alter_table("signals") as b:
        b.alter_column("detector", type_=sa.String(16), existing_type=sa.String(32), existing_nullable=False)
