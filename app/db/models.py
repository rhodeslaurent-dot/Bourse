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
