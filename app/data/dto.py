"""Data transfer objects shared by every market-data provider (CLAUDE.md rule 2).

Every price carries ``source``, ``market_timestamp`` (UTC, market time), ``received_at``,
``processed_at``, ``price_type``, ``market_perimeter`` and ``data_status``. The status is derived
from the provider's *declared* delay and the age of the **market** timestamp, never from the
download time: a 15-minute delayed quote fetched one second ago is still ``delayed``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PriceType(StrEnum):
    LAST = "last"
    BID = "bid"
    ASK = "ask"
    CLOSE = "close"
    THEORETICAL_OPEN = "theoretical_open"


class DataStatus(StrEnum):
    REALTIME = "realtime"
    DELAYED = "delayed"
    EOD = "eod"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class MarketPerimeter(StrEnum):
    PRIMARY = "primary"  # venue's own book (Euronext, Xetra)
    CBOE_CONSOLIDATED = "cboe_consolidated"  # EODHD WebSocket (Cboe Europe BXE/CXE/DXE)
    UNKNOWN = "unknown"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    D1 = "1d"


def _aware(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return v.astimezone(UTC)


class _Stamped(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    market_perimeter: MarketPerimeter
    received_at: datetime
    processed_at: datetime
    data_status: DataStatus

    @field_validator("received_at", "processed_at")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        return _aware(v)


class Quote(_Stamped):
    symbol: str
    isin: str | None = None
    market_timestamp: datetime | None  # None only when data_status is unavailable
    price_type: PriceType
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume_cum: int | None = None
    currency: str | None = None
    declared_delay_minutes: int | None = None

    @field_validator("market_timestamp")
    @classmethod
    def _tz_market(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _aware(v)


class EodBar(_Stamped):
    symbol: str
    isin: str | None = None
    day: date
    open: float
    high: float
    low: float
    close: float
    adj_close: float | None = None
    volume: int
    official: bool = True  # False = provisional close (screener 17:45), reconciled at 06:45


class IntradayBar(_Stamped):
    symbol: str
    isin: str | None = None
    market_timestamp: datetime  # bar start, UTC
    tf: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete_bar: bool = True  # False = a gap was detected inside/around this bar (never back-filled)
    trades: int = 0

    @field_validator("market_timestamp")
    @classmethod
    def _tz_market(cls, v: datetime) -> datetime:
        return _aware(v)


class ScreenerRow(BaseModel):
    """One row of a delayed screener scan (tradingview). Always ``delayed``."""

    model_config = ConfigDict(frozen=True)

    ticker: str  # EXCHANGE:SYMBOL
    name: str | None = None
    close: float | None = None
    open: float | None = None
    volume: float | None = None
    change_pct: float | None = None
    gap_pct: float | None = None
    relative_volume_10d: float | None = None
    average_volume_10d: float | None = None
    market_cap: float | None = None
    sector: str | None = None
    earnings_release_next_date: date | None = None
    extra: dict[str, float | str | None] = Field(default_factory=dict)
    received_at: datetime
    data_status: DataStatus = DataStatus.DELAYED
    source: str = "tradingview_screener"


def classify_status(
    market_timestamp: datetime | None,
    now: datetime,
    declared_delay_minutes: int,
    stale_after_minutes: dict[str, int],
    session_open: bool = True,
) -> DataStatus:
    """Status from the *declared* delay and the market-timestamp age.

    - no market timestamp → unavailable
    - declared delay > 0 → delayed (stale if older than ``stale_after_minutes['delayed']`` + delay)
    - declared delay 0 → realtime (stale if older than ``stale_after_minutes['realtime']``)
    - outside the session a last quote that is the close is ``eod`` (caller sets session_open)
    """
    if market_timestamp is None:
        return DataStatus.UNAVAILABLE
    age = now - market_timestamp
    if not session_open:
        return DataStatus.EOD
    if declared_delay_minutes > 0:
        limit = timedelta(minutes=stale_after_minutes.get("delayed", 20) + declared_delay_minutes)
        return DataStatus.STALE if age > limit else DataStatus.DELAYED
    limit = timedelta(minutes=stale_after_minutes.get("realtime", 3))
    return DataStatus.STALE if age > limit else DataStatus.REALTIME


def fresh_enough_for_intraday_order(quote: Quote, now: datetime, max_market_age_minutes: int) -> bool:
    """Rule 2: an intraday order sheet needs ``now − market_timestamp ≤ max`` **and** realtime data."""
    if quote.market_timestamp is None or quote.data_status != DataStatus.REALTIME:
        return False
    return now - quote.market_timestamp <= timedelta(minutes=max_market_age_minutes)
