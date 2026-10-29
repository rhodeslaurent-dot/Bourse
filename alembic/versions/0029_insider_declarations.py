"""docs/34 §34.8 — archive of the managers' dealing declarations published by the AMF (BDIF), role only.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insider_declarations",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("doc_id", sa.String(32), nullable=False),
        sa.Column("market_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("info_date", sa.Date(), nullable=True),
        sa.Column("issuer_name", sa.String(160), nullable=True),
        sa.Column("issuer_token", sa.String(16), nullable=True),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("isin_source", sa.String(12), nullable=True),
        sa.Column("role", sa.String(40), nullable=True),
        sa.Column("related_role", sa.String(40), nullable=True),
        sa.Column("linked_person", sa.Boolean(), nullable=True),
        sa.Column("nature", sa.String(16), nullable=True),
        sa.Column("nature_detail", sa.String(80), nullable=True),
        sa.Column("tx_date", sa.Date(), nullable=True),
        sa.Column("tx_date_last", sa.Date(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("venue", sa.String(64), nullable=True),
        sa.Column("n_transactions", sa.Integer(), nullable=True),
        sa.Column("transactions", sa.JSON(), nullable=True),
        sa.Column("pdf_url", sa.String(500), nullable=True),
        sa.Column("parse_status", sa.String(20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "doc_id", name="uq_insider_declarations_source_doc"),
    )
    op.create_index("ix_insider_declarations_isin", "insider_declarations", ["isin"])
    op.create_index("ix_insider_declarations_market_timestamp", "insider_declarations", ["market_timestamp"])


def downgrade() -> None:
    op.drop_table("insider_declarations")
