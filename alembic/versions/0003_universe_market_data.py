"""Phase 1 (C) — instruments, universe, prices_eod, features_daily, market_regime, watchlist (docs/13 §13.1–13.2).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer, "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("isin", sa.String(12), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("ticker_local", sa.String(24), nullable=True),
        sa.Column("ticker_eodhd", sa.String(32), nullable=True, index=True),
        sa.Column("ticker_saxo", sa.String(32), nullable=True),
        sa.Column("ticker_tv", sa.String(32), nullable=True),
        sa.Column("exchange", sa.String(16), nullable=True),
        sa.Column("mic", sa.String(8), nullable=False, index=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("country_of_domicile", sa.String(2), nullable=True),
        sa.Column("sector", sa.String(64), nullable=True),
        sa.Column("industry", sa.String(64), nullable=True),
        sa.Column("index_memberships", sa.JSON, nullable=True),
        sa.Column("market_cap_eur", sa.Float, nullable=True),
        sa.Column("adv_eur_20", sa.Float, nullable=True),
        sa.Column("pea_eligible", sa.Boolean, nullable=True),
        sa.Column("pea_confidence", sa.Float, nullable=True),
        sa.Column("pea_source", sa.String(32), nullable=True),
        sa.Column("pea_available_boursobank", sa.Boolean, nullable=True),
        sa.Column("pea_order_types_ok", sa.Boolean, nullable=True),
        sa.Column("srd_status", sa.String(10), nullable=False, server_default="unknown"),
        sa.Column("srd_confirmed_by_user", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("earnings_next_date", sa.Date, nullable=True),
        sa.Column("earnings_source", sa.String(32), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("in_universe", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("universe_reasons", sa.JSON, nullable=True),
        sa.Column("small_cap", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "instrument_aliases",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("alias", sa.String(128), nullable=False, index=True),
        sa.UniqueConstraint("provider", "alias", name="uq_instrument_aliases_provider_alias"),
    )
    op.create_table(
        "universe_snapshots",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("filters", sa.JSON, nullable=True),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("added", sa.JSON, nullable=True),
        sa.Column("removed", sa.JSON, nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "universe_members",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("snapshot_id", sa.Integer, nullable=False, index=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("adv_eur", sa.Float, nullable=True),
        sa.Column("market_cap", sa.Float, nullable=True),
        sa.Column("rs_rank", sa.Float, nullable=True),
        sa.Column("flags", sa.JSON, nullable=True),
    )
    op.create_table(
        "pea_eligibility",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("eligible", sa.Boolean, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("checked_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "srd_eligibility",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("checked_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_by_user_at", _TS, nullable=True),
    )
    op.create_table(
        "prices_eod",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("adj_close", sa.Float, nullable=True),
        sa.Column("volume", sa.BigInteger, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("market_perimeter", sa.String(20), nullable=False, server_default="primary"),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("data_status", sa.String(12), nullable=False, server_default="eod"),
        sa.Column("official", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("isin", "day", name="uq_prices_eod_isin_day"),
    )
    op.create_index("ix_prices_eod_isin_day", "prices_eod", ["isin", "day"])
    op.create_table(
        "features_daily",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        *[
            sa.Column(c, sa.Float, nullable=True)
            for c in [
                "atr14",
                "adr20",
                "mm10",
                "mm20",
                "mm50",
                "mm200",
                "rvol",
                "rs_1m",
                "rs_3m",
                "rs_6m",
                "rs_12m",
                "rs_score",
                "rs_rank",
                "pivot_60",
                "high_52w",
                "consolidation_20",
                "ti65",
                "rsi14",
            ]
        ],
        sa.Column("sessions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("gap_in_data", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("computed_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("isin", "day", name="uq_features_daily_isin_day"),
    )
    op.create_table(
        "market_regime",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("cac_vs_mm50", sa.Boolean, nullable=True),
        sa.Column("stoxx_vs_mm50", sa.Boolean, nullable=True),
        sa.Column("breadth_mm50", sa.Float, nullable=True),
        sa.Column("distribution_days", sa.Integer, nullable=True),
        sa.Column("vol_pct", sa.Float, nullable=True),
        sa.Column("regime", sa.String(8), nullable=False),
        sa.Column("details", sa.JSON, nullable=True),
    )
    op.create_table(
        "watchlist",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("added_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("levels", sa.JSON, nullable=True),
        sa.Column("rs_rank", sa.Float, nullable=True),
    )
    op.create_table(
        "fx_rates",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("pair", sa.String(7), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("rate", sa.Float, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.UniqueConstraint("pair", "day", name="uq_fx_rates_pair_day"),
    )
    op.create_table(
        "earnings_calendar",
        sa.Column("id", _BIGID, primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("when", sa.String(8), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("isin", "day", "source", name="uq_earnings_isin_day_source"),
    )


def downgrade() -> None:
    for t in [
        "earnings_calendar",
        "fx_rates",
        "watchlist",
        "market_regime",
        "features_daily",
        "prices_eod",
        "srd_eligibility",
        "pea_eligibility",
        "universe_members",
        "universe_snapshots",
        "instrument_aliases",
        "instruments",
    ]:
        op.drop_table(t)
