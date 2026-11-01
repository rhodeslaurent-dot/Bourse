"""Lot L1 (docs/36 §36.9, ADR-017) — archive of the regulated releases of the AMF flow (metadata only).

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "official_filings",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("also_in", sa.String(16), nullable=True),
        sa.Column("also_in_time_ok", sa.Boolean(), nullable=True),
        sa.Column("diffuser", sa.String(8), nullable=False),
        sa.Column("uin_id", sa.String(32), nullable=False),
        sa.Column("issuer_name", sa.String(200), nullable=True),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("lei", sa.String(20), nullable=True),
        sa.Column("ticker", sa.String(16), nullable=True),
        sa.Column("type_code", sa.String(6), nullable=True),
        sa.Column("type_codes", sa.JSON(), nullable=True),
        sa.Column("family", sa.String(20), nullable=False),
        sa.Column("subtype_label", sa.String(160), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("disclosure", sa.String(4), nullable=True),
        sa.Column("update_status", sa.String(4), nullable=True),
        sa.Column("update_ref", sa.String(40), nullable=True),
        sa.Column("market_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amf_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_source", sa.String(8), nullable=True),
        sa.Column("time_precision", sa.String(10), nullable=False),
        sa.Column("raw_market_time", sa.String(40), nullable=True),
        sa.Column("tz_label", sa.String(8), nullable=True),
        sa.Column("tz_label_ok", sa.Boolean(), nullable=True),
        sa.Column("pdf_path", sa.String(200), nullable=True),
        sa.Column("data_status", sa.String(12), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("diffuser", "uin_id", name="uq_official_filings_diffuser_uin"),
    )
    op.create_index("ix_official_filings_isin", "official_filings", ["isin"])
    op.create_index("ix_official_filings_family", "official_filings", ["family"])
    op.create_index("ix_official_filings_market_timestamp", "official_filings", ["market_timestamp"])
    op.create_table(
        "official_filing_packages",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("name", sa.String(40), nullable=False, unique=True),
        sa.Column("n_xml", sa.Integer(), nullable=False),
        sa.Column("n_new", sa.Integer(), nullable=False),
        sa.Column("n_unreadable", sa.Integer(), nullable=False),
        sa.Column("size_bytes", _ID, nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("official_filing_packages")
    op.drop_table("official_filings")
