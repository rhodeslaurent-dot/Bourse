"""Rule 2: status derives from declared delay + market-timestamp age, never from download time."""

from datetime import UTC, datetime, timedelta

import pytest

from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote, classify_status, fresh_enough_for_intraday_order

NOW = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
STALE = {"realtime": 3, "delayed": 20}


def test_delayed_source_stays_delayed_even_if_downloaded_now():
    ts = NOW - timedelta(minutes=16)
    assert classify_status(ts, NOW, 15, STALE) == DataStatus.DELAYED
    assert classify_status(NOW - timedelta(seconds=5), NOW, 15, STALE) == DataStatus.DELAYED


def test_realtime_then_stale():
    assert classify_status(NOW - timedelta(seconds=30), NOW, 0, STALE) == DataStatus.REALTIME
    assert classify_status(NOW - timedelta(minutes=4), NOW, 0, STALE) == DataStatus.STALE


def test_delayed_becomes_stale_after_delay_plus_threshold():
    assert classify_status(NOW - timedelta(minutes=34), NOW, 15, STALE) == DataStatus.DELAYED
    assert classify_status(NOW - timedelta(minutes=36), NOW, 15, STALE) == DataStatus.STALE


def test_unavailable_and_eod():
    assert classify_status(None, NOW, 0, STALE) == DataStatus.UNAVAILABLE
    assert classify_status(NOW - timedelta(hours=3), NOW, 0, STALE, session_open=False) == DataStatus.EOD


def _quote(status, age_s, source="eodhd_ws"):
    return Quote(
        source=source,
        market_perimeter=MarketPerimeter.CBOE_CONSOLIDATED,
        received_at=NOW,
        processed_at=NOW,
        data_status=status,
        symbol="MC.PA",
        market_timestamp=NOW - timedelta(seconds=age_s),
        price_type=PriceType.LAST,
        last=612.4,
    )


def test_intraday_order_requires_realtime_and_fresh_market_timestamp():
    assert fresh_enough_for_intraday_order(_quote(DataStatus.REALTIME, 60), NOW, 3)
    assert not fresh_enough_for_intraday_order(_quote(DataStatus.REALTIME, 200), NOW, 3)
    assert not fresh_enough_for_intraday_order(_quote(DataStatus.DELAYED, 5), NOW, 3)  # fresh download, delayed data


def test_naive_timestamp_refused():
    with pytest.raises(ValueError):
        Quote(
            source="x",
            market_perimeter=MarketPerimeter.PRIMARY,
            received_at=datetime(2026, 1, 1),
            processed_at=NOW,
            data_status=DataStatus.EOD,
            symbol="X",
            market_timestamp=None,
            price_type=PriceType.CLOSE,
        )
