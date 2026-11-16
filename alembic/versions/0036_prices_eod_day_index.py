"""Index on prices_eod.day (16/11/2026): the queries by day alone (last close of the home page, health of the sources,
a venue's closes of a day) scanned the 54 million rows; under load the home page timed out through Cloudflare (524).

Built at the application's start (no request is served meanwhile); a few minutes on the production base.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations  # noqa: I001

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_prices_eod_day", "prices_eod", ["day"])


def downgrade() -> None:
    op.drop_index("ix_prices_eod_day", table_name="prices_eod")
