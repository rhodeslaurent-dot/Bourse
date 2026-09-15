"""Phase 1 (a) — accounts, orders, trades, positions, stops, portfolio_state (docs/13 §13.4).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("cash", sa.Float, nullable=True),
        sa.Column("cash_updated_at", _TS, nullable=True),
        sa.Column("cash_source", sa.String(16), nullable=True),
        sa.Column("broker_account_key", sa.String(64), nullable=True),
        sa.UniqueConstraint("broker", "type", name="uq_accounts_broker_type"),
    )
    op.create_table(
        "orders",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("qty_filled", sa.Integer, nullable=False, server_default="0"),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("limit_price", sa.Float, nullable=True),
        sa.Column("trigger_price", sa.Float, nullable=True),
        sa.Column("validity", sa.String(16), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="entered"),
        sa.Column("broker_order_id", sa.String(64), nullable=True, index=True),
        sa.Column("proposal_id", sa.Integer, nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="web"),
        sa.Column("entered_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("note", sa.Text, nullable=True),
    )
    op.create_table(
        "trades",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("fx_rate", sa.Float, nullable=False, server_default="1"),
        sa.Column("fees", sa.Float, nullable=False, server_default="0"),
        sa.Column("ttf", sa.Float, nullable=False, server_default="0"),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("mode", sa.String(8), nullable=False, server_default="comptant"),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("declared_uid", sa.String(32), nullable=True, unique=True),
        sa.Column("broker_fill_id", sa.String(64), nullable=True, unique=True),
        sa.Column("matched_trade_id", sa.Integer, nullable=True),
        sa.Column("matched_at", _TS, nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("order_id", sa.Integer, nullable=True),
        sa.Column("proposal_id", sa.Integer, nullable=True),
        sa.Column("detector", sa.String(16), nullable=True),
        sa.Column("tags", sa.JSON, nullable=True),
        sa.Column("position_id", sa.Integer, nullable=True, index=True),
        sa.Column("deleted_at", _TS, nullable=True),
    )
    op.create_table(
        "positions",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("qty_held", sa.Integer, nullable=False, server_default="0"),
        sa.Column("qty_ordered", sa.Integer, nullable=False, server_default="0"),
        sa.Column("qty_protected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("execution_state", sa.String(16), nullable=False, server_default="order_entered"),
        sa.Column("protection_state", sa.String(24), nullable=False, server_default="unprotected"),
        sa.Column("stop_initial", sa.Float, nullable=True),
        sa.Column("stop_current", sa.Float, nullable=True),
        sa.Column("stop_type", sa.String(8), nullable=True),
        sa.Column("mode", sa.String(8), nullable=False, server_default="comptant"),
        sa.Column("opened_at", _TS, nullable=True),
        sa.Column("closed_at", _TS, nullable=True),
        sa.Column("protected_at", _TS, nullable=True),
        sa.Column("exit_reason", sa.String(16), nullable=True),
        sa.Column("crossed_since", _TS, nullable=True),
        sa.Column("last_price", sa.Float, nullable=True),
        sa.Column("last_price_at", _TS, nullable=True),
        sa.Column("last_price_status", sa.String(12), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.UniqueConstraint("account_id", "isin", "opened_at", name="uq_positions_account_isin_open"),
    )
    op.create_table(
        "stops",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("position_id", sa.Integer, nullable=False, index=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("level", sa.Float, nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("qty_covered", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("broker_status", sa.String(10), nullable=False, server_default="unknown"),
        sa.Column("broker_confirmed_at", _TS, nullable=True),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="web"),
    )
    op.create_table(
        "portfolio_state",
        sa.Column("account_id", sa.Integer, primary_key=True),
        sa.Column("cash", sa.Float, nullable=True),
        sa.Column("positions_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("orders_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("synced_at", _TS, nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default=""),
        sa.Column("declarative", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "broker_sync_runs",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("broker", sa.String(16), nullable=False),
        sa.Column("ts", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("positions_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("diffs", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_table(
        "cash_snapshots",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("account_id", sa.Integer, nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("cash", sa.Float, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.UniqueConstraint("account_id", "day", name="uq_cash_snapshots_account_day"),
    )


def downgrade() -> None:
    for t in [
        "cash_snapshots",
        "broker_sync_runs",
        "portfolio_state",
        "stops",
        "positions",
        "trades",
        "orders",
        "accounts",
    ]:
        op.drop_table(t)
