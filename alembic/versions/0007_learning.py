"""Learning loop and global assistant (docs/18) — signal_outcomes, learning_reviews,
learning_questions, learning_suggestions, trader_memory; assistant_messages.page.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "signal_outcomes",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("proposal_id", sa.Integer, nullable=False, unique=True),
        sa.Column("signal_id", sa.Integer, nullable=True, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("computed_at", _TS, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("final", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("decision_group", sa.String(16), nullable=False),
        sa.Column("ref_price", sa.Float, nullable=True),
        sa.Column("entry_day", sa.Date, nullable=True),
        sa.Column("entry_price", sa.Float, nullable=True),
        sa.Column("exit_day", sa.Date, nullable=True),
        sa.Column("exit_price", sa.Float, nullable=True),
        sa.Column("exit_reason", sa.String(12), nullable=True),
        sa.Column("r_gross", sa.Float, nullable=True),
        sa.Column("r_net", sa.Float, nullable=True),
        sa.Column("costs_counted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ret_pct", sa.Float, nullable=True),
        sa.Column("mfe_pct", sa.Float, nullable=True),
        sa.Column("mae_pct", sa.Float, nullable=True),
        sa.Column("mfe_r", sa.Float, nullable=True),
        sa.Column("mae_r", sa.Float, nullable=True),
        sa.Column("ret_1d", sa.Float, nullable=True),
        sa.Column("ret_5d", sa.Float, nullable=True),
        sa.Column("ret_20d", sa.Float, nullable=True),
        sa.Column("last_bar_day", sa.Date, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
    )
    op.create_table(
        "learning_reviews",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", _TS, nullable=False, index=True),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("findings", sa.JSON, nullable=True),
        sa.Column("dossier", sa.JSON, nullable=True),
        sa.Column("rejected", sa.JSON, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("model", sa.String(48), nullable=True),
    )
    op.create_table(
        "learning_questions",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("review_id", sa.Integer, nullable=True, index=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("why", sa.Text, nullable=True),
        sa.Column("kind", sa.String(8), nullable=False, server_default="texte"),
        sa.Column("choices", sa.JSON, nullable=True),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("answered_at", _TS, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
    )
    op.create_table(
        "learning_suggestions",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("review_id", sa.Integer, nullable=True, index=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("expected_effect", sa.Text, nullable=True),
        sa.Column("params_key", sa.String(128), nullable=True),
        sa.Column("current_value", sa.JSON, nullable=True),
        sa.Column("proposed_value", sa.JSON, nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("user_comment", sa.Text, nullable=True),
        sa.Column("decided_at", _TS, nullable=True),
    )
    op.create_table(
        "trader_memory",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("ref", sa.String(64), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.add_column("assistant_messages", sa.Column("page", sa.String(48), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_messages", "page")
    for t in ("trader_memory", "learning_suggestions", "learning_questions", "learning_reviews", "signal_outcomes"):
        op.drop_table(t)
