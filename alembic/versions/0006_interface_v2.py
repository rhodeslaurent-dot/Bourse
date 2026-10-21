"""Interface v2 (docs/17) — notes, workflow_steps, assistant_messages, kpi_daily.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False, index=True),
        sa.Column("ref", sa.String(64), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_at", _TS, nullable=False),
        sa.UniqueConstraint("scope", "ref", name="uq_notes_scope_ref"),
    )
    op.create_table(
        "workflow_steps",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("key", sa.String(48), nullable=False),
        sa.Column("done_at", _TS, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.UniqueConstraint("day", "key", name="uq_workflow_steps_day_key"),
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("ts", _TS, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=True, index=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.JSON, nullable=True),
        sa.Column("answered_by", sa.String(16), nullable=False),
        sa.Column("context", sa.JSON, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("model", sa.String(48), nullable=True),
    )
    op.create_table(
        "kpi_daily",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("account_id", sa.Integer, nullable=True),
        sa.Column("equity", sa.Float, nullable=True),
        sa.Column("cash", sa.Float, nullable=True),
        sa.Column("positions_value", sa.Float, nullable=True),
        sa.Column("latent_pnl", sa.Float, nullable=True),
        sa.Column("realized_pnl_cum", sa.Float, nullable=True),
        sa.Column("incomplete", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("computed_at", _TS, nullable=False),
        sa.UniqueConstraint("day", "account_id", name="uq_kpi_daily_day_account"),
    )


def downgrade() -> None:
    for t in ("kpi_daily", "assistant_messages", "workflow_steps", "notes"):
        op.drop_table(t)
