"""17/11/2026 — the screener returns at most ``limit_per_request`` rows per market (by volume): 1 754 values found
out of 4 799. Extra pages are read only when configured, spaced, and a cut is always reported."""

from __future__ import annotations

import json

import httpx

from app.data.providers.tradingview_screener import TradingViewScreener

COLS = ["name", "close"]


def _client(total: int, asked: list[list[int]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        lo, hi = json.loads(request.content)["range"]
        asked.append([lo, hi])
        data = [{"s": f"EURONEXT:V{k}", "d": [f"V{k}", 10.0]} for k in range(lo, min(hi, total))]
        return httpx.Response(200, json={"totalCount": total, "data": data})

    return httpx.Client(base_url="https://scanner.example", transport=httpx.MockTransport(handler))


def test_one_page_by_default_and_the_cut_is_reported():
    asked: list[list[int]] = []
    tv = TradingViewScreener(client=_client(25, asked), min_interval_seconds=0, limit_per_request=10)
    rows = tv.scan(["france"], columns=COLS)
    assert len(rows) == 10 and asked == [[0, 10]]
    assert tv.truncated == {"france": (10, 25)}


def test_extra_pages_when_configured_until_the_market_is_read():
    asked: list[list[int]] = []
    tv = TradingViewScreener(client=_client(25, asked), min_interval_seconds=0, limit_per_request=10,
                             max_pages_per_market=5, page_pause_seconds=0)  # fmt: skip
    rows = tv.scan(["france"], columns=COLS)
    assert len(rows) == 25 and asked == [[0, 10], [10, 20], [20, 30]]  # stops when the market is read
    assert tv.truncated == {} and tv.requests_made == 3
    asked.clear()
    tv2 = TradingViewScreener(client=_client(25, asked), min_interval_seconds=0, limit_per_request=10,
                              max_pages_per_market=2, page_pause_seconds=0)  # fmt: skip
    assert len(tv2.scan(["france"], columns=COLS)) == 20 and tv2.truncated == {"france": (20, 25)}


def test_only_the_followed_venues_are_asked_and_a_refused_filter_is_dropped():
    """18/11/2026: « germany » mixes 37 727 rows of every German venue; the tool follows Xetra only."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if any(f["left"] == "exchange" for f in body["filter"]) and body["markets"] == ["uk"]:
            return httpx.Response(400, json={"error": "unknown field"})
        return httpx.Response(200, json={"totalCount": 1, "data": [{"s": "XETR:SAP", "d": ["SAP", 1.0]}]})

    client = httpx.Client(base_url="https://scanner.example", transport=httpx.MockTransport(handler))
    tv = TradingViewScreener(client=client, min_interval_seconds=0, limit_per_request=10)
    tv.scan(["germany", "uk"], columns=COLS, exchanges={"germany": ["XETR"], "uk": ["LSE"]})
    assert {"left": "exchange", "operation": "in_range", "right": ["XETR"]} in sent[0]["filter"]
    assert {"left": "type", "operation": "equal", "right": "stock"} in sent[0]["filter"]
    assert all(f["left"] != "exchange" for f in sent[-1]["filter"])  # uk: refused, read without the filter
    assert tv.filter_refused == {"uk"}
