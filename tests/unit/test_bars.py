"""Tick → 1-minute bar aggregation; gaps are marked (complete_bar=False), never filled."""

from datetime import UTC, datetime, timedelta

from app.data.bars import BarAggregator, Tick

T0 = datetime(2026, 9, 15, 7, 0, 0, tzinfo=UTC)


def tick(sec: int, price: float, size: int = 10, sym: str = "MC.PA") -> Tick:
    ts = T0 + timedelta(seconds=sec)
    return Tick(sym, ts, price, size, ts + timedelta(milliseconds=200))


def test_bars_close_on_minute_change():
    agg = BarAggregator(source="eodhd_ws")
    assert agg.add(tick(1, 100)) is None
    assert agg.add(tick(30, 101, 5)) is None
    bar = agg.add(tick(61, 99))
    assert bar is not None and bar.market_timestamp == T0 and bar.open == 100 and bar.high == 101 and bar.close == 101
    assert bar.volume == 15 and bar.trades == 2 and bar.complete_bar


def test_missing_minute_marks_next_bar_incomplete():
    agg = BarAggregator(source="eodhd_ws")
    agg.add(tick(1, 100))
    agg.add(tick(61, 100))  # closes minute 0
    bar = agg.add(tick(200, 100))  # minute 3: minute 2 missing
    assert bar is not None and bar.market_timestamp == T0 + timedelta(minutes=1) and bar.complete_bar
    bar3 = agg.flush(T0 + timedelta(minutes=5))[0]
    assert bar3.market_timestamp == T0 + timedelta(minutes=3) and not bar3.complete_bar


def test_disconnection_marks_gap_on_current_and_next_bar():
    agg = BarAggregator(source="eodhd_ws")
    agg.add(tick(1, 100))
    agg.mark_gap()
    bar = agg.add(tick(61, 100))
    assert not bar.complete_bar
    bar2 = agg.add(tick(121, 100))
    assert not bar2.complete_bar  # first bar after reconnection is flagged
    bar3 = agg.add(tick(181, 100))
    assert bar3.complete_bar


def test_symbols_are_independent():
    agg = BarAggregator(source="eodhd_ws")
    agg.add(tick(1, 100, sym="A"))
    agg.add(tick(2, 50, sym="B"))
    a = agg.add(tick(61, 100, sym="A"))
    assert a.symbol == "A" and len(agg.closed) == 1
