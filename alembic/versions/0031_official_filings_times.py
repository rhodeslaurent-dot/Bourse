"""ADR-017 revised (02/11/2026) — raw filing time and the time given by the second source of a release.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("official_filings", sa.Column("raw_amf_time", sa.String(40), nullable=True))
    op.add_column("official_filings", sa.Column("other_market_timestamp", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("official_filings") as b:
        b.drop_column("other_market_timestamp")
        b.drop_column("raw_amf_time")
