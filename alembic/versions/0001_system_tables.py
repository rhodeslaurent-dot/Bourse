"""Phase 0 system tables (docs/13 §13.1, §13.3, §13.6).

Revision ID: 0001
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "params_versions",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("effective_from", sa.Date, nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("yaml_text", sa.Text, nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_params_versions_sha"),
    )
    op.create_table(
        "param_overrides",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "market_calendar",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("mic", sa.String(8), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("open_time", sa.Time, nullable=True),
        sa.Column("close_time", sa.Time, nullable=True),
        sa.Column("source", sa.String(255), nullable=False, server_default=""),
        sa.UniqueConstraint("mic", "day", name="uq_market_calendar_mic_day"),
    )
    op.create_table(
        "srd_calendar",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("liquidation_date", sa.Date, nullable=False, unique=True),
        sa.Column("settlement_date", sa.Date, nullable=True),
        sa.Column("source", sa.String(255), nullable=False, server_default=""),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "availability",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
    )
    op.create_table(
        "jobs_runs",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("job", sa.String(64), nullable=False, index=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rows", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("params_version", sa.String(32), nullable=True),
    )
    op.create_table(
        "data_freshness",
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quota_used", sa.Integer, nullable=True),
        sa.Column("quota_limit", sa.Integer, nullable=True),
    )
    op.create_table(
        "alerts",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("level", sa.String(4), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("account_id", sa.Integer, nullable=True),
        sa.Column("proposal_id", sa.Integer, nullable=True),
        sa.Column("position_id", sa.Integer, nullable=True),
        sa.Column("incident_id", sa.String(128), nullable=True, index=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("message_hash", sa.String(64), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repeat_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON, nullable=True),
    )
    op.create_table(
        "telegram_events",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False),
        sa.Column("authorised", sa.Boolean, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("callback_data", sa.String(128), nullable=True),
        sa.Column("handled", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "llm_calls",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False, index=True),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("cached", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("purpose", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    for t in [
        "llm_calls",
        "telegram_events",
        "alerts",
        "data_freshness",
        "jobs_runs",
        "availability",
        "srd_calendar",
        "market_calendar",
        "param_overrides",
        "params_versions",
    ]:
        op.drop_table(t)
