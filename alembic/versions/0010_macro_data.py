"""Free macro data (docs/22 §22.7, lot 2.2) — macro_points (dashboard series), macro_events
(economic calendar), instruments.saxo_uic (quote at each analysis, R19).

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "macro_points",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("indicator", sa.String(32), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_timestamp", _TS, nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("data_status", sa.String(12), nullable=False),
        sa.UniqueConstraint("indicator", "day", name="uq_macro_points_indicator_day"),
    )
    op.create_index("ix_macro_points_indicator_day", "macro_points", ["indicator", "day"])
    op.create_table(
        "macro_events",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", _TS, nullable=False, index=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("impact", sa.String(12), nullable=False),
        sa.Column("forecast", sa.String(32), nullable=True),
        sa.Column("previous", sa.String(32), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("received_at", _TS, nullable=False),
        sa.UniqueConstraint("ts", "currency", "title", name="uq_macro_events_ts_ccy_title"),
    )
    with op.batch_alter_table("instruments") as b:
        b.add_column(sa.Column("saxo_uic", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("instruments") as b:
        b.drop_column("saxo_uic")
    op.drop_table("macro_events")
    op.drop_index("ix_macro_points_indicator_day", table_name="macro_points")
    op.drop_table("macro_points")
