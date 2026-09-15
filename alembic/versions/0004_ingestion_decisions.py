"""Phase 1 (D) — news, newsletters, signals, proposals, decisions, snapshots, llm_cache (docs/13 §13.2–13.3).

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("prompt_hash", sa.String(64), primary_key=True),
        sa.Column("response", sa.JSON, nullable=False),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "news_items",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("guid", sa.String(255), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("published_at", _TS, nullable=True, index=True),
        sa.Column("fetched_at", _TS, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("isins", sa.JSON, nullable=True),
        sa.Column("classification", sa.JSON, nullable=True),
        sa.Column("classified_by", sa.String(16), nullable=True),
        sa.Column("llm_prompt_version", sa.String(16), nullable=True),
        sa.Column("llm_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("classified_at", _TS, nullable=True),
    )
    op.create_table(
        "newsletter_items",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, index=True),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("sent_at", _TS, nullable=True),
        sa.Column("received_at", _TS, nullable=False, index=True),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("parsed", sa.JSON, nullable=True),
        sa.Column("parsed_by", sa.String(16), nullable=True),
        sa.Column("llm_prompt_version", sa.String(16), nullable=True),
    )
    op.create_table(
        "newsletter_values",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("item_id", sa.Integer, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=True, index=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("levels", sa.JSON, nullable=True),
        sa.Column("p_open", sa.Float, nullable=True),
        sa.Column("p_recv", sa.Float, nullable=True),
        sa.Column("p_recv_market_ts", _TS, nullable=True),
        sa.Column("p_recv_status", sa.String(12), nullable=True),
        sa.Column("close_prev", sa.Float, nullable=True),
        sa.Column("gap_open", sa.Float, nullable=True),
        sa.Column("drift_since_open", sa.Float, nullable=True),
        sa.Column("signal_id", sa.Integer, nullable=True),
    )
    op.create_table(
        "premarket_watch",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("news_id", sa.Integer, nullable=True),
        sa.Column("expected_direction", sa.String(8), nullable=False),
        sa.Column("magnitude", sa.String(8), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("added_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("day", "isin", "source", name="uq_premarket_day_isin_source"),
    )
    op.create_table(
        "signals",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("detector", sa.String(16), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("ts", _TS, nullable=False, index=True),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False, server_default="long"),
        sa.Column("entry_low", sa.Float, nullable=True),
        sa.Column("entry_high", sa.Float, nullable=True),
        sa.Column("stop_initial", sa.Float, nullable=True),
        sa.Column("targets", sa.JSON, nullable=True),
        sa.Column("horizon", sa.String(12), nullable=True),
        sa.Column("evidence", sa.JSON, nullable=True),
        sa.Column("data_status", sa.String(12), nullable=False),
        sa.Column("catalyst_news_id", sa.Integer, nullable=True),
        sa.Column("universe_snapshot_id", sa.Integer, nullable=True),
        sa.Column("params_version", sa.String(32), nullable=False),
        sa.Column("external_refs", sa.JSON, nullable=True),
        sa.Column("deleted_at", _TS, nullable=True),
    )
    op.create_table(
        "proposals",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("signal_id", sa.Integer, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("account_recommended", sa.String(10), nullable=True),
        sa.Column("account_alt", sa.JSON, nullable=True),
        sa.Column("entry_zone", sa.JSON, nullable=True),
        sa.Column("stop", sa.Float, nullable=True),
        sa.Column("targets", sa.JSON, nullable=True),
        sa.Column("horizon", sa.String(12), nullable=True),
        sa.Column("size_eur", sa.Float, nullable=True),
        sa.Column("shares", sa.Integer, nullable=True),
        sa.Column("risk_eur", sa.Float, nullable=True),
        sa.Column("cost_estimate", sa.JSON, nullable=True),
        sa.Column("gates", sa.JSON, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("to_verify", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", _TS, nullable=True),
        sa.Column("sent_at", _TS, nullable=True),
        sa.Column("channels", sa.JSON, nullable=True),
        sa.Column("deleted_at", _TS, nullable=True),
    )
    op.create_table(
        "decisions",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("proposal_id", sa.Integer, nullable=False, index=True),
        sa.Column("ts", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("via", sa.String(10), nullable=False),
    )
    op.create_table(
        "decision_snapshots",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("proposal_id", sa.Integer, nullable=False, unique=True),
        sa.Column("taken_at", _TS, nullable=False),
        sa.Column("quotes", sa.JSON, nullable=True),
        sa.Column("features", sa.JSON, nullable=True),
        sa.Column("regime", sa.JSON, nullable=True),
        sa.Column("portfolio_state", sa.JSON, nullable=True),
        sa.Column("params_version", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    for t in [
        "decision_snapshots",
        "decisions",
        "proposals",
        "signals",
        "premarket_watch",
        "newsletter_values",
        "newsletter_items",
        "news_items",
        "llm_cache",
    ]:
        op.drop_table(t)
