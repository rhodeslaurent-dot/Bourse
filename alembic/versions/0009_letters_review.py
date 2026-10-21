"""Daily letters review (docs/20) — letter_reviews, letter_opinions, letter_validations.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "letter_reviews",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", _TS, nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("synthesis", sa.Text, nullable=True),
        sa.Column("item_ids", sa.JSON, nullable=True),
        sa.Column("dossier", sa.JSON, nullable=True),
        sa.Column("rejected", sa.JSON, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("model", sa.String(48), nullable=True),
    )
    op.create_table(
        "letter_opinions",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("review_id", sa.Integer, nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=True, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("item_ids", sa.JSON, nullable=True),
        sa.Column("stance", sa.String(10), nullable=False),
        sa.Column("explicit", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("catalyst", sa.Text, nullable=True),
        sa.Column("horizon", sa.String(16), nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("quote", sa.Text, nullable=True),
    )
    op.create_table(
        "letter_validations",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("review_id", sa.Integer, nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("target_type", sa.String(12), nullable=False),
        sa.Column("target_ref", sa.String(32), nullable=False),
        sa.Column("isin", sa.String(12), nullable=True, index=True),
        sa.Column("verdict", sa.String(12), nullable=False),
        sa.Column("justification", sa.Text, nullable=True),
        sa.Column("item_ids", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    for t in ("letter_validations", "letter_opinions", "letter_reviews"):
        op.drop_table(t)
