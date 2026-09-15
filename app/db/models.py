"""System tables for phase 0 (docs/13 §13.1, §13.3 availability, §13.6).

Conventions: bigint ids, timestamptz in UTC, ``created_at``. Business tables (instruments,
prices, signals…) arrive with their phase and their own Alembic migration.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ParamsVersion(Base):
    __tablename__ = "params_versions"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    yaml_text: Mapped[str] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("sha256", name="uq_params_versions_sha"),)


class ParamOverride(Base):
    __tablename__ = "param_overrides"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketCalendarRow(Base):
    __tablename__ = "market_calendar"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    mic: Mapped[str] = mapped_column(String(8))
    day: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))  # open | closed | half_day
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    source: Mapped[str] = mapped_column(String(255), default="")
    __table_args__ = (UniqueConstraint("mic", "day", name="uq_market_calendar_mic_day"),)


class SrdCalendarRow(Base):
    __tablename__ = "srd_calendar"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    liquidation_date: Mapped[date] = mapped_column(Date, unique=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(255), default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class AvailabilityRow(Base):
    """Explicit mode overrides (docs/13 §13.3 ``availability``)."""

    __tablename__ = "availability"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    mode: Mapped[str] = mapped_column(String(16))
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # schedule | telegram | web | api


class JobRun(Base):
    __tablename__ = "jobs_runs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    job: Mapped[str] = mapped_column(String(64), index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # running | ok | error | skipped
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DataFreshness(Base):
    __tablename__ = "data_freshness"
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quota_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quota_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    level: Mapped[str] = mapped_column(String(4))  # P1..P4
    kind: Mapped[str] = mapped_column(String(64))
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    incident_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(32))
    message_hash: Mapped[str] = mapped_column(String(64))
    day: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class TelegramEvent(Base):
    """Every inbound Telegram interaction (button click, command), authorised or not."""

    __tablename__ = "telegram_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    authorised: Mapped[bool] = mapped_column(Boolean)
    kind: Mapped[str] = mapped_column(String(32))  # command | callback | message
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_data: Mapped[str | None] = mapped_column(String(128), nullable=True)
    handled: Mapped[bool] = mapped_column(Boolean, default=False)


class LlmCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    purpose: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------------------------------------
# Phase 1 — comptes, ordres, exécutions, positions, stops (docs/13 §13.4)
# ---------------------------------------------------------------------------------------------


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    broker: Mapped[str] = mapped_column(String(16))  # boursobank | saxo
    type: Mapped[str] = mapped_column(String(16))  # pea | cto_cash | cto_srd
    label: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    cash: Mapped[float | None] = mapped_column(nullable=True)
    cash_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cash_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # saxo_api | manual | csv
    broker_account_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (UniqueConstraint("broker", "type", name="uq_accounts_broker_type"),)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    qty_filled: Mapped[int] = mapped_column(Integer, default=0)
    order_type: Mapped[str] = mapped_column(String(16))
    limit_price: Mapped[float | None] = mapped_column(nullable=True)
    trigger_price: Mapped[float | None] = mapped_column(nullable=True)
    validity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str] = mapped_column(String(20), default="entered")
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="web")
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Trade(Base):
    """Executions: declared (provisional) and confirmed (broker), matched on one line."""

    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    fx_rate: Mapped[float] = mapped_column(default=1.0)
    fees: Mapped[float] = mapped_column(default=0.0)
    ttf: Mapped[float] = mapped_column(default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(8), default="comptant")
    kind: Mapped[str] = mapped_column(String(10))  # declared | confirmed
    declared_uid: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    broker_fill_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    matched_trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detector: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)  # e.g. ["discretionary?"]
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # soft delete only


class PositionRow(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    qty_held: Mapped[int] = mapped_column(Integer, default=0)
    qty_ordered: Mapped[int] = mapped_column(Integer, default=0)
    qty_protected: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float] = mapped_column(default=0.0)
    execution_state: Mapped[str] = mapped_column(String(16), default="order_entered")
    protection_state: Mapped[str] = mapped_column(String(24), default="unprotected")
    stop_initial: Mapped[float | None] = mapped_column(nullable=True)
    stop_current: Mapped[float | None] = mapped_column(nullable=True)
    stop_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    mode: Mapped[str] = mapped_column(String(8), default="comptant")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    crossed_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[float | None] = mapped_column(nullable=True)
    last_price_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    __table_args__ = (UniqueConstraint("account_id", "isin", "opened_at", name="uq_positions_account_isin_open"),)


class StopRow(Base):
    __tablename__ = "stops"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    position_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[float] = mapped_column()
    order_type: Mapped[str] = mapped_column(String(8))  # seuil | plage
    qty_covered: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # initial | trailing | manual | linked_if_done
    broker_status: Mapped[str] = mapped_column(String(10), default="unknown")
    broker_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="web")


class PortfolioState(Base):
    """Latest synced state per account with its age (docs/13 §13.7)."""

    __tablename__ = "portfolio_state"
    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash: Mapped[float | None] = mapped_column(nullable=True)
    positions_hash: Mapped[str] = mapped_column(String(64), default="")
    orders_hash: Mapped[str] = mapped_column(String(64), default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="")
    declarative: Mapped[bool] = mapped_column(Boolean, default=True)  # PEA between two imports


class BrokerSyncRun(Base):
    __tablename__ = "broker_sync_runs"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    broker: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(16))
    positions_count: Mapped[int] = mapped_column(Integer, default=0)
    diffs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CashSnapshot(Base):
    __tablename__ = "cash_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[date] = mapped_column(Date)
    cash: Mapped[float] = mapped_column()
    source: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint("account_id", "day", name="uq_cash_snapshots_account_day"),)


# ---------------------------------------------------------------------------------------------
# Phase 1 (C) — référentiel, univers, données de marché, régime, watchlist (docs/13 §13.1–13.2)
# ---------------------------------------------------------------------------------------------


class Instrument(Base):
    __tablename__ = "instruments"
    isin: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    ticker_local: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ticker_eodhd: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ticker_saxo: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticker_tv: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mic: Mapped[str] = mapped_column(String(8), index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    country_of_domicile: Mapped[str | None] = mapped_column(String(2), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_memberships: Mapped[list | None] = mapped_column(JSON, nullable=True)
    market_cap_eur: Mapped[float | None] = mapped_column(nullable=True)
    adv_eur_20: Mapped[float | None] = mapped_column(nullable=True)
    pea_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pea_confidence: Mapped[float | None] = mapped_column(nullable=True)
    pea_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pea_available_boursobank: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pea_order_types_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    srd_status: Mapped[str] = mapped_column(String(10), default="unknown")
    srd_confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    earnings_next_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    earnings_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    in_universe: Mapped[bool] = mapped_column(Boolean, default=False)
    universe_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    small_cap: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstrumentAlias(Base):
    __tablename__ = "instrument_aliases"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    alias: Mapped[str] = mapped_column(String(128), index=True)
    __table_args__ = (UniqueConstraint("provider", "alias", name="uq_instrument_aliases_provider_alias"),)


class UniverseSnapshot(Base):
    __tablename__ = "universe_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    version: Mapped[str] = mapped_column(String(32))
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    added: Mapped[list | None] = mapped_column(JSON, nullable=True)
    removed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UniverseMember(Base):
    __tablename__ = "universe_members"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    adv_eur: Mapped[float | None] = mapped_column(nullable=True)
    market_cap: Mapped[float | None] = mapped_column(nullable=True)
    rs_rank: Mapped[float | None] = mapped_column(nullable=True)
    flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PeaEligibilityRow(Base):
    __tablename__ = "pea_eligibility"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    source: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SrdEligibilityRow(Base):
    __tablename__ = "srd_eligibility"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    status: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(32))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_by_user_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PriceEod(Base):
    __tablename__ = "prices_eod"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12))
    day: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column()
    high: Mapped[float] = mapped_column()
    low: Mapped[float] = mapped_column()
    close: Mapped[float] = mapped_column()
    adj_close: Mapped[float | None] = mapped_column(nullable=True)
    volume: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32))
    market_perimeter: Mapped[str] = mapped_column(String(20), default="primary")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_status: Mapped[str] = mapped_column(String(12), default="eod")
    official: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("isin", "day", name="uq_prices_eod_isin_day"),)


class FeatureDaily(Base):
    __tablename__ = "features_daily"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12))
    day: Mapped[date] = mapped_column(Date)
    atr14: Mapped[float | None] = mapped_column(nullable=True)
    adr20: Mapped[float | None] = mapped_column(nullable=True)
    mm10: Mapped[float | None] = mapped_column(nullable=True)
    mm20: Mapped[float | None] = mapped_column(nullable=True)
    mm50: Mapped[float | None] = mapped_column(nullable=True)
    mm200: Mapped[float | None] = mapped_column(nullable=True)
    rvol: Mapped[float | None] = mapped_column(nullable=True)
    rs_1m: Mapped[float | None] = mapped_column(nullable=True)
    rs_3m: Mapped[float | None] = mapped_column(nullable=True)
    rs_6m: Mapped[float | None] = mapped_column(nullable=True)
    rs_12m: Mapped[float | None] = mapped_column(nullable=True)
    rs_score: Mapped[float | None] = mapped_column(nullable=True)
    rs_rank: Mapped[float | None] = mapped_column(nullable=True)
    pivot_60: Mapped[float | None] = mapped_column(nullable=True)
    high_52w: Mapped[float | None] = mapped_column(nullable=True)
    consolidation_20: Mapped[float | None] = mapped_column(nullable=True)
    ti65: Mapped[float | None] = mapped_column(nullable=True)
    rsi14: Mapped[float | None] = mapped_column(nullable=True)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    gap_in_data: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("isin", "day", name="uq_features_daily_isin_day"),)


class MarketRegimeRow(Base):
    __tablename__ = "market_regime"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cac_vs_mm50: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stoxx_vs_mm50: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    breadth_mm50: Mapped[float | None] = mapped_column(nullable=True)
    distribution_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vol_pct: Mapped[float | None] = mapped_column(nullable=True)
    regime: Mapped[str] = mapped_column(String(8))
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WatchlistRow(Base):
    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(String(32))  # momentum | external | manual | a_l_affut
    source: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    levels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rs_rank: Mapped[float | None] = mapped_column(nullable=True)


class FxRate(Base):
    __tablename__ = "fx_rates"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    pair: Mapped[str] = mapped_column(String(7))
    day: Mapped[date] = mapped_column(Date)
    rate: Mapped[float] = mapped_column()
    source: Mapped[str] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("pair", "day", name="uq_fx_rates_pair_day"),)


class EarningsCalendarRow(Base):
    __tablename__ = "earnings_calendar"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    day: Mapped[date] = mapped_column(Date)
    when: Mapped[str] = mapped_column(String(8), default="unknown")  # bmo | amc | unknown
    source: Mapped[str] = mapped_column(String(32))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("isin", "day", "source", name="uq_earnings_isin_day_source"),)


# ---------------------------------------------------------------------------------------------
# Phase 1 (D) — news, newsletters, signaux externes, propositions, décisions, instantanés
# ---------------------------------------------------------------------------------------------


class LlmCache(Base):
    __tablename__ = "llm_cache"
    prompt_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    guid: Mapped[str] = mapped_column(String(255))
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    isins: Mapped[list | None] = mapped_column(JSON, nullable=True)
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classified_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # llm | rules
    llm_prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    llm_cost_usd: Mapped[float] = mapped_column(default=0.0)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NewsletterItem(Base):
    __tablename__ = "newsletter_items"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[str] = mapped_column(String(255))
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    subject: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)  # kept private, never shown in full in the UI
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    llm_prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)


class NewsletterValue(Base):
    __tablename__ = "newsletter_values"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[str] = mapped_column(String(8))
    levels: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    p_open: Mapped[float | None] = mapped_column(nullable=True)
    p_recv: Mapped[float | None] = mapped_column(nullable=True)
    p_recv_market_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    p_recv_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    close_prev: Mapped[float | None] = mapped_column(nullable=True)
    gap_open: Mapped[float | None] = mapped_column(nullable=True)
    drift_since_open: Mapped[float | None] = mapped_column(nullable=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PremarketWatch(Base):
    __tablename__ = "premarket_watch"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    isin: Mapped[str] = mapped_column(String(12))
    news_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_direction: Mapped[str] = mapped_column(String(8))
    magnitude: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(32))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("day", "isin", "source", name="uq_premarket_day_isin_source"),)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    detector: Mapped[str] = mapped_column(String(16), index=True)  # d1..d6 | external
    source: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    direction: Mapped[str] = mapped_column(String(8), default="long")
    entry_low: Mapped[float | None] = mapped_column(nullable=True)
    entry_high: Mapped[float | None] = mapped_column(nullable=True)
    stop_initial: Mapped[float | None] = mapped_column(nullable=True)
    targets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    horizon: Mapped[str | None] = mapped_column(String(12), nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    data_status: Mapped[str] = mapped_column(String(12))
    catalyst_news_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    universe_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    params_version: Mapped[str] = mapped_column(String(32))
    external_refs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    action: Mapped[str] = mapped_column(String(10))  # BUY | WATCH | HOLD | SELL | REDUCE | NO_TRADE
    account_recommended: Mapped[str | None] = mapped_column(String(10), nullable=True)
    account_alt: Mapped[list | None] = mapped_column(JSON, nullable=True)
    entry_zone: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stop: Mapped[float | None] = mapped_column(nullable=True)
    targets: Mapped[list | None] = mapped_column(JSON, nullable=True)
    horizon: Mapped[str | None] = mapped_column(String(12), nullable=True)
    size_eur: Mapped[float | None] = mapped_column(nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_eur: Mapped[float | None] = mapped_column(nullable=True)
    cost_estimate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | expired | taken | watch | ignored
    to_verify: Mapped[bool] = mapped_column(Boolean, default=False)  # stale portfolio state / delayed data
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    channels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    proposal_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decision: Mapped[str] = mapped_column(String(16))  # seen | watch | order_entered | ignored | snoozed
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    via: Mapped[str] = mapped_column(String(10))  # telegram | web | api


class DecisionSnapshot(Base):
    """Frozen data used for a proposal (CLAUDE.md rule 10) — never updated."""

    __tablename__ = "decision_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    proposal_id: Mapped[int] = mapped_column(Integer, unique=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quotes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    features: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    regime: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    portfolio_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    params_version: Mapped[str] = mapped_column(String(32))
