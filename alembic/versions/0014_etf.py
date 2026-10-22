"""ETFs in the universe (phase 3, docs/22 §22.5 rank 2) — instruments.kind.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("instruments") as b:
        b.add_column(sa.Column("kind", sa.String(8), nullable=False, server_default="stock"))


def downgrade() -> None:
    with op.batch_alter_table("instruments") as b:
        b.drop_column("kind")
