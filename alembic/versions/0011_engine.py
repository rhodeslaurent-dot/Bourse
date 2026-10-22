"""Proposal engine (lot 2.3, docs/22) — proposals.rank / details / thesis, letter_reviews.index_view
(option C of the market gate: obstacle and advice published by the letters on the index).

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as b:
        b.add_column(sa.Column("rank", sa.Integer, nullable=True))
        b.add_column(sa.Column("details", sa.JSON, nullable=True))
        b.add_column(sa.Column("thesis", sa.JSON, nullable=True))
    with op.batch_alter_table("letter_reviews") as b:
        b.add_column(sa.Column("index_view", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("letter_reviews") as b:
        b.drop_column("index_view")
    with op.batch_alter_table("proposals") as b:
        b.drop_column("thesis")
        b.drop_column("details")
        b.drop_column("rank")
