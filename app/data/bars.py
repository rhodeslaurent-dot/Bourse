"""Aggregate ticks into 1-minute bars and mark gaps (docs/04 §4.5). Pure, testable.

A gap (disconnection, missing minute) is **marked**, never back-filled with data finalised later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.data.dto import DataStatus, IntradayBar, MarketPerimeter, Timeframe


@dataclass(frozen=True)
class Tick:
    symbol: str
    market_timestamp: datetime
    price: float
    size: int
    received_at: datetime


@dataclass
class _Open:
    start: datetime
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: int
    n: int
    first_received: datetime
    gap_inside: bool = False


@dataclass
class BarAggregator:
    source: str
    perimeter: MarketPerimeter = MarketPerimeter.CBOE_CONSOLIDATED
    _open: dict[str, _Open] = field(default_factory=dict)
    _last_minute: dict[str, datetime] = field(default_factory=dict)
    _gap_pending: set[str] = field(default_factory=set)
    closed: list[IntradayBar] = field(default_factory=list)

    @staticmethod
    def minute_of(ts: datetime) -> datetime:
        return ts.astimezone(UTC).replace(second=0, microsecond=0)

    def mark_gap(self, symbol: str | None = None) -> None:
        """Called on disconnection/reconnection: the next bar(s) are flagged incomplete."""
        targets = [symbol] if symbol else list(self._open) + list(self._last_minute)
        for s in set(targets):
            self._gap_pending.add(s)
            if s in self._open:
                self._open[s].gap_inside = True

    def add(self, tick: Tick) -> IntradayBar | None:
        m = self.minute_of(tick.market_timestamp)
        cur = self._open.get(tick.symbol)
        closed: IntradayBar | None = None
        if cur is not None and m > cur.start:
            closed = self._close(tick.symbol, cur, tick.received_at)
            cur = None
        if cur is None:
            last = self._last_minute.get(tick.symbol)
            gap = tick.symbol in self._gap_pending or (last is not None and m - last > timedelta(minutes=1))
            self._gap_pending.discard(tick.symbol)
            self._open[tick.symbol] = _Open(
                m, tick.price, tick.price, tick.price, tick.price, tick.size, 1, tick.received_at, gap
            )
        else:
            cur.h = max(cur.h, tick.price)
            cur.l = min(cur.l, tick.price)
            cur.c = tick.price
            cur.v += tick.size
            cur.n += 1
        return closed

    def flush(self, now: datetime) -> list[IntradayBar]:
        out = []
        for s, cur in list(self._open.items()):
            if self.minute_of(now) > cur.start:
                out.append(self._close(s, cur, now))
        return out

    def _close(self, symbol: str, cur: _Open, received_at: datetime) -> IntradayBar:
        bar = IntradayBar(
            source=self.source,
            market_perimeter=self.perimeter,
            received_at=received_at,
            processed_at=datetime.now(UTC),
            data_status=DataStatus.REALTIME,
            symbol=symbol,
            market_timestamp=cur.start,
            tf=Timeframe.M1,
            open=cur.o,
            high=cur.h,
            low=cur.l,
            close=cur.c,
            volume=cur.v,
            trades=cur.n,
            complete_bar=not cur.gap_inside,
        )
        self.closed.append(bar)
        self._last_minute[symbol] = cur.start
        self._open.pop(symbol, None)
        return bar
