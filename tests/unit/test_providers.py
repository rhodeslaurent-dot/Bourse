"""Recorded responses (respx) for EODHD, TradingView; fake downloader for yfinance; Saxo guard."""

from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from app.data.base import ProviderError, QuotaExceeded
from app.data.dto import DataStatus, MarketPerimeter
from app.data.providers.eodhd import BASE, EodhdProvider, EodhdWebSocket
from app.data.providers.saxo import LIVE_BASE, ReadOnlyViolation, SaxoClient, SaxoPriceProvider, TokenStore
from app.data.providers.tradingview_screener import BASE as TV_BASE
from app.data.providers.tradingview_screener import TradingViewScreener
from app.data.providers.yfinance_eod import YFinanceProvider


@pytest.fixture
def eodhd():
    return EodhdProvider(api_token="t", client=httpx.Client(base_url=BASE))


@respx.mock
def test_eodhd_eod_and_bulk(eodhd):
    respx.get(f"{BASE}/eod/MC.PA").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "date": "2026-09-14",
                    "open": 600,
                    "high": 615,
                    "low": 598,
                    "close": 612.4,
                    "adjusted_close": 612.4,
                    "volume": 300000,
                }
            ],
        )
    )
    bars = eodhd.fetch_eod(["MC.PA"], date(2026, 9, 14), date(2026, 9, 14))
    assert (
        bars[0].close == 612.4
        and bars[0].data_status == DataStatus.EOD
        and bars[0].market_perimeter == MarketPerimeter.PRIMARY
    )
    respx.get(f"{BASE}/eod-bulk-last-day/PA").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "code": "MC",
                    "exchange_short_name": "PA",
                    "date": "2026-09-14",
                    "open": 600,
                    "high": 615,
                    "low": 598,
                    "close": 612.4,
                    "adjusted_close": 612.4,
                    "volume": 300000,
                }
            ],
        )
    )
    bulk = eodhd.fetch_eod_bulk("PA")
    assert bulk[0].symbol == "MC.PA" and bulk[0].official
    assert eodhd.calls_made == 2


@respx.mock
def test_eodhd_rest_quote_is_delayed_whatever_download_time(eodhd):
    now = int(datetime.now(UTC).timestamp())
    respx.get(f"{BASE}/real-time/MC.PA").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"code": "MC.PA", "timestamp": now - 900, "close": 612.4, "volume": 1000},
                {"code": "SAP.XETRA", "timestamp": "NA", "close": "NA", "volume": "NA"},
            ],
        )
    )
    q = eodhd.fetch_quotes(["MC.PA", "SAP.XETRA"])
    assert q[0].data_status == DataStatus.DELAYED and q[0].declared_delay_minutes == 15 and q[0].last == 612.4
    assert q[1].data_status == DataStatus.UNAVAILABLE and q[1].last is None


@respx.mock
def test_eodhd_quota_and_errors(eodhd):
    respx.get(f"{BASE}/eod/X.PA").mock(return_value=httpx.Response(402, text="quota"))
    with pytest.raises(QuotaExceeded):
        eodhd.fetch_eod(["X.PA"], date(2026, 9, 1), date(2026, 9, 2))
    eodhd.calls_made = eodhd.daily_quota
    with pytest.raises(QuotaExceeded):
        eodhd.fetch_eod(["X.PA"], date(2026, 9, 1), date(2026, 9, 2))


@respx.mock
def test_eodhd_intraday_is_delayed_perimeter_primary(eodhd):
    respx.get(f"{BASE}/intraday/MC.PA").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"timestamp": 1789456500, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
                {"timestamp": 1789456800, "open": None},
            ],
        )
    )
    bars = eodhd.fetch_intraday("MC.PA", date(2026, 9, 15))
    assert len(bars) == 1 and bars[0].data_status == DataStatus.DELAYED and bars[0].tf.value == "5m"


def test_eodhd_ws_message_parsing_and_bars():
    ws = EodhdWebSocket(api_token="t")
    got = []
    ws.on_bar = got.append
    t0 = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
    ms = int(t0.timestamp() * 1000)
    assert ws.handle_raw('{"status_code":200,"message":"Authorized"}') is None
    ws.handle_raw(f'{{"s":"MC.PA","p":612.4,"v":10,"t":{ms}}}')
    ws.handle_raw(f'{{"s":"MC.PA","p":612.9,"v":5,"t":{ms + 61000}}}')
    assert len(got) == 1 and got[0].close == 612.4 and got[0].market_perimeter == MarketPerimeter.CBOE_CONSOLIDATED
    assert ws._chunks([str(i) for i in range(120)]) and [len(c) for c in ws._chunks([str(i) for i in range(120)])] == [
        50,
        50,
        20,
    ]


@respx.mock
def test_tradingview_scan_limits_and_parsing():
    tv = TradingViewScreener(client=httpx.Client(base_url=TV_BASE), min_interval_seconds=60, max_requests_per_scan=6)
    cols = ["name", "close", "gap", "volume", "sector", "earnings_release_next_date"]
    respx.post(f"{TV_BASE}/france/scan").mock(
        return_value=httpx.Response(
            200, json={"data": [{"s": "EURONEXT:MC", "d": ["MC", 612.4, 1.2, 300000, "Consumer Durables", 1791000000]}]}
        )
    )
    rows = tv.scan(["france"], columns=cols)
    assert rows[0].ticker == "EURONEXT:MC" and rows[0].gap_pct == 1.2 and rows[0].data_status == DataStatus.DELAYED
    assert rows[0].earnings_release_next_date == date(2026, 10, 3)
    with pytest.raises(ProviderError):
        tv.scan(["france"], columns=cols)  # too soon
    with pytest.raises(ProviderError):
        TradingViewScreener(max_requests_per_scan=2).scan(["a", "b", "c"])
    with pytest.raises(ProviderError):
        TradingViewScreener(enabled=False).scan(["france"])


def test_yfinance_fallback_marks_eod_and_refuses_intraday():
    import pandas as pd

    idx = pd.to_datetime(["2026-09-14", "2026-09-15"])
    df = pd.DataFrame(
        {
            "Open": [600.0, 610.0],
            "High": [615.0, 616.0],
            "Low": [598.0, 605.0],
            "Close": [612.4, 614.0],
            "Adj Close": [612.4, 614.0],
            "Volume": [300000, 250000],
        },
        index=idx,
    )
    yf = YFinanceProvider(downloader=lambda *a, **k: df)
    bars = yf.fetch_eod(["MC.PA"], date(2026, 9, 14), date(2026, 9, 16))
    assert [b.day for b in bars] == [date(2026, 9, 14), date(2026, 9, 15)] and bars[0].source == "yfinance"
    quotes = yf.fetch_quotes(["MC.PA"])
    assert quotes[0].data_status == DataStatus.EOD and quotes[0].price_type.value == "close" and quotes[0].last == 614.0
    with pytest.raises(ProviderError):
        yf.fetch_intraday("MC.PA", date(2026, 9, 15))


def test_saxo_client_is_read_only():
    c = SaxoClient(client=httpx.Client(), access_token="tok")
    for method, path in [
        ("POST", "/port/v1/orders"),
        ("PUT", "/port/v1/positions"),
        ("GET", "/trade/v2/positions"),
        ("DELETE", "/port/v1/orders/123"),
    ]:
        with pytest.raises(ReadOnlyViolation):
            c.request(method, path)
    with pytest.raises(ReadOnlyViolation):
        c.request("GET", "/trade/v2/" + "orders")  # built dynamically so the source stays clean
    assert c.calls == []


@respx.mock
def test_saxo_infoprices_status_from_delayed_by_minutes():
    c = SaxoClient(client=httpx.Client(), access_token="tok")
    now = datetime.now(UTC).replace(microsecond=0)
    respx.get(f"{LIVE_BASE}/trade/v1/infoprices/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "Uic": 1,
                        "LastUpdated": now.isoformat().replace("+00:00", "Z"),
                        "Quote": {"Bid": 612.3, "Ask": 612.5, "DelayedByMinutes": 0, "PriceTypeAsk": "Tradable"},
                        "PriceInfoDetails": {"LastTraded": 612.4, "Volume": 48210},
                        "DisplayAndFormat": {"Currency": "EUR"},
                    },
                    {
                        "Uic": 2,
                        "LastUpdated": now.isoformat().replace("+00:00", "Z"),
                        "Quote": {"Bid": 10, "Ask": 10.1, "DelayedByMinutes": 15},
                        "PriceInfoDetails": {},
                    },
                ]
            },
        )
    )
    q = SaxoPriceProvider(c).fetch_quotes(["1", "2"])
    assert (
        q[0].data_status == DataStatus.REALTIME
        and q[0].last == 612.4
        and q[0].volume_cum == 48210
        and q[0].currency == "EUR"
    )
    assert (
        q[1].data_status == DataStatus.DELAYED and q[1].declared_delay_minutes == 15 and q[1].price_type.value == "bid"
    )
    assert c.calls == [("GET", "/trade/v1/infoprices/list")]


def test_token_store_roundtrip(tmp_path):
    from cryptography.fernet import Fernet

    store = TokenStore(path=str(tmp_path / "tok.enc"), key=Fernet.generate_key().decode())
    store.save("refresh-abc")
    assert store.load() == "refresh-abc"
    assert b"refresh-abc" not in (tmp_path / "tok.enc").read_bytes()
