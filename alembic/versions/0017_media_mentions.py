"""docs/23 §23.7 — values cited by media sources (podcasts, radio, press).

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_mentions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("item_guid", sa.String(255), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=True, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("stance", sa.String(8), nullable=False),
        sa.Column("speaker", sa.String(128), nullable=True),
        sa.Column("snippet", sa.String(300), nullable=True),
        sa.Column("price_ref", sa.Float(), nullable=True),
        sa.Column("price_ref_day", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "item_guid", "name", name="uq_media_mentions_item_name"),
    )


def downgrade() -> None:
    op.drop_table("media_mentions")
