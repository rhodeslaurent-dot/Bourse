"""docs/27 — backtest runs and simulated trades.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("params_version", sa.String(32), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("end", sa.Date(), nullable=False),
        sa.Column("instruments", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=True),
    )
    op.create_table(
        "backtest_trades",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("detector", sa.String(32), nullable=False),
        sa.Column("signal_day", sa.Date(), nullable=False),
        sa.Column("entry_day", sa.Date(), nullable=False),
        sa.Column("exit_day", sa.Date(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("exit", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("warned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("regime", sa.String(12), nullable=True),
        sa.Column("r_gross", sa.Float(), nullable=False),
        sa.Column("r_net", sa.Float(), nullable=False),
        sa.Column("mfe_r", sa.Float(), nullable=False),
        sa.Column("mae_r", sa.Float(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backtest_trades")
    op.drop_table("backtest_runs")
