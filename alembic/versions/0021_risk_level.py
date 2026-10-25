"""docs/28 — reference risk set in the interface, daily risk level (recommended, override).

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "risk_settings",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("capital_pilote_eur", sa.Float(), nullable=False),
        sa.Column("reference_risk_pct", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("set_via", sa.String(10), nullable=False, server_default="web"),
    )
    op.create_table(
        "risk_dial_daily",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("day", sa.Date(), nullable=False, unique=True),
        sa.Column("recommended", sa.Float(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=True),
        sa.Column("explanation", sa.JSON(), nullable=True),
        sa.Column("override", sa.Float(), nullable=True),
        sa.Column("override_reason", sa.String(300), nullable=True),
        sa.Column("override_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("risk_dial_daily")
    op.drop_table("risk_settings")
