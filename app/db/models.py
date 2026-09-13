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
