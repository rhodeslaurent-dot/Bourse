"""Lot M0 (docs/39) — ``instruments.kind`` from 8 to 16 characters: the universe refresh writes « crypto_etp »
(10 characters) for a configured crypto ETP; PostgreSQL would refuse the row.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("instruments") as b:
        b.alter_column("kind", existing_type=sa.String(8), type_=sa.String(16), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("instruments") as b:
        b.alter_column("kind", existing_type=sa.String(16), type_=sa.String(8), existing_nullable=False)
