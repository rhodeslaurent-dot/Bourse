"""docs/24 v2 — investment committee deliberations (observation mode).

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "committee_runs",
        sa.Column("id", _ID, primary_key=True),
        sa.Column("proposal_id", sa.Integer(), nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("dossier_hash", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False, server_default="observation"),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("action_comite", sa.String(24), nullable=True),
        sa.Column("confiance_comite", sa.String(8), nullable=True),
        sa.Column("effect", sa.JSON(), nullable=True),
        sa.Column("dossier", sa.JSON(), nullable=True),
        sa.Column("premortem", sa.JSON(), nullable=True),
        sa.Column("verdict", sa.JSON(), nullable=True),
        sa.Column("rejected", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("models", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("proposal_id", "dossier_hash", name="uq_committee_runs_proposal_hash"),
    )


def downgrade() -> None:
    op.drop_table("committee_runs")
