"""EODHD — primary EOD / intraday / real-time source (docs/04 §4.1, §4.5).

REST: EOD, bulk last day per exchange, intraday 5 min (delayed, finalised after close), delayed
"real-time" endpoint (15–20 min). WebSocket ``ws/eu``: Cboe Europe consolidated trades → 1-minute
bars with ``complete_bar``. Quota tracked by the caller via ``calls_made``.
"""

from __future__ import annotations  # noqa: I001

import asyncio
import json
import logging
import os
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

import httpx

from app.data.base import MarketDataProvider, ProviderError, QuotaExceeded
from app.data.bars import BarAggregator, Tick
from app.data.dto import DataStatus, EodBar, IntradayBar, MarketPerimeter, PriceType, Quote, Timeframe, classify_status

log = logging.getLogger("bourse.data.eodhd")
BASE = "https://eodhd.com/api"
WS_URL = "wss://ws.eodhistoricaldata.com/ws/eu"


class EodhdProvider(MarketDataProvider):
    name = "eodhd"
    declared_delay_minutes = 15  # REST "real-time" endpoint is delayed 15–20 min (docs/04); WS is realtime

    def __init__(
        self,
        api_token: str | None = None,
        client: httpx.Client | None = None,
        stale_after: dict[str, int] | None = None,
        daily_quota: int = 100_000,
    ) -> None:
        self.token = api_token or os.environ.get("EODHD_API_TOKEN", "")
        self.client = client or httpx.Client(base_url=BASE, timeout=30)
        self.stale_after = stale_after or {"realtime": 3, "delayed": 20}
        self.daily_quota = daily_quota
        self.calls_made = 0

    # --- REST -------------------------------------------------------------------------------
    def _get(self, path: str, **params: object) -> object:
        if not self.token:
            raise ProviderError("EODHD_API_TOKEN absent")
        if self.calls_made >= self.daily_quota:
            raise QuotaExceeded("quota EODHD journalier atteint")
        params = {"api_token": self.token, "fmt": "json", **params}
        self.calls_made += 1
        r = self.client.get(path, params=params)
        if r.status_code == 402 or r.status_code == 429:
            raise QuotaExceeded(f"EODHD {r.status_code}: {r.text[:120]}")
        if r.status_code >= 400:
            raise ProviderError(f"EODHD {r.status_code} on {path}: {r.text[:120]}")
        return r.json()

    def fetch_eod(self, symbols: Sequence[str], start: date, end: date) -> list[EodBar]:
        out: list[EodBar] = []
        now = datetime.now(UTC)
        for sym in symbols:
            rows = self._get(f"/eod/{sym}", **{"from": start.isoformat(), "to": end.isoformat(), "period": "d"})
            for r in rows:  # type: ignore[union-attr]
                out.append(
                    EodBar(
                        source=self.name,
                        market_perimeter=MarketPerimeter.PRIMARY,
                        received_at=now,
                        processed_at=datetime.now(UTC),
                        data_status=DataStatus.EOD,
                        symbol=sym,
                        day=date.fromisoformat(r["date"]),
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                        adj_close=float(r["adjusted_close"]) if r.get("adjusted_close") is not None else None,
                        volume=int(r.get("volume") or 0),
                    )
                )
        return out

    def exchanges(self) -> list[dict[str, str]]:
        """``exchanges-list``: Code, Name, Country, Currency (to diagnose an unknown exchange code)."""
        rows = self._get("/exchanges-list/")
        return list(rows) if isinstance(rows, list) else []

    def exchange_symbols(
        self, exchange: str, types: tuple[str, ...] = ("common stock", "stock", "")
    ) -> list[dict[str, str]]:
        """``exchange-symbol-list``: Code, Name, Country, Exchange, Currency, Type, Isin (``types``
        lower-cased: add ``"etf"`` to include ETFs)."""
        rows = self._get(f"/exchange-symbol-list/{exchange}")
        return [r for r in rows if str(r.get("Type", "")).lower() in types]  # type: ignore[union-attr]

    def delisted_symbols(
        self, exchange: str, types: tuple[str, ...] = ("common stock", "stock", "")
    ) -> list[dict[str, str]]:
        """``exchange-symbol-list/{ex}?delisted=1``: the listings that left the exchange (research registration
        n° 2 needs them: a universe without its delisted values flatters every result)."""
        rows = self._get(f"/exchange-symbol-list/{exchange}", delisted=1)
        return [r for r in rows if str(r.get("Type", "")).lower() in types] if isinstance(rows, list) else []

    def index_symbols(self) -> list[dict[str, str]]:
        """Every index EODHD lists (``exchange-symbol-list/INDX``, any type): to find the code of an index."""
        rows = self._get("/exchange-symbol-list/INDX")
        return list(rows) if isinstance(rows, list) else []

    def fetch_eod_bulk(self, exchange: str, day: date | None = None) -> list[EodBar]:
        """One call for a whole exchange (``eod-bulk-last-day``)."""
        params: dict[str, object] = {}
        if day:
            params["date"] = day.isoformat()
        rows = self._get(f"/eod-bulk-last-day/{exchange}", **params)
        now = datetime.now(UTC)
        return [
            EodBar(
                source=self.name,
                market_perimeter=MarketPerimeter.PRIMARY,
                received_at=now,
                processed_at=datetime.now(UTC),
                data_status=DataStatus.EOD,
                symbol=f"{r['code']}.{r.get('exchange_short_name', exchange)}",
                day=date.fromisoformat(r["date"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                adj_close=float(r["adjusted_close"]) if r.get("adjusted_close") is not None else None,
                volume=int(r.get("volume") or 0),
            )
            for r in rows  # type: ignore[union-attr]
        ]

    def fetch_intraday(self, symbol: str, day: date, tf: Timeframe = Timeframe.M5) -> list[IntradayBar]:
        """Historical intraday (delayed, finalised after close). Never used to fill a live gap."""
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)
        rows = self._get(
            f"/intraday/{symbol}", interval=tf.value, **{"from": int(start.timestamp()), "to": int(end.timestamp())}
        )
        now = datetime.now(UTC)
        return [
            IntradayBar(
                source=self.name,
                market_perimeter=MarketPerimeter.PRIMARY,
                received_at=now,
                processed_at=datetime.now(UTC),
                data_status=DataStatus.DELAYED,
                symbol=symbol,
                market_timestamp=datetime.fromtimestamp(int(r["timestamp"]), tz=UTC),
                tf=tf,
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=int(r.get("volume") or 0),
            )
            for r in rows  # type: ignore[union-attr]
            if r.get("open") is not None
        ]

    def fetch_quotes(self, symbols: Sequence[str]) -> list[Quote]:
        """Delayed REST quotes: status is *delayed* whatever the download time (rule 2)."""
        if not symbols:
            return []
        first, rest = symbols[0], symbols[1:]
        params: dict[str, object] = {}
        if rest:
            params["s"] = ",".join(rest)
        data = self._get(f"/real-time/{first}", **params)
        rows = data if isinstance(data, list) else [data]
        now = datetime.now(UTC)
        out = []
        for r in rows:
            ts = r.get("timestamp")
            mts = datetime.fromtimestamp(int(ts), tz=UTC) if ts not in (None, "NA") else None
            status = classify_status(mts, now, self.declared_delay_minutes, self.stale_after)
            close = r.get("close")
            out.append(
                Quote(
                    source=f"{self.name}_rest",
                    market_perimeter=MarketPerimeter.PRIMARY,
                    received_at=now,
                    processed_at=datetime.now(UTC),
                    data_status=status if close not in (None, "NA") else DataStatus.UNAVAILABLE,
                    symbol=str(r.get("code", first)),
                    market_timestamp=mts,
                    price_type=PriceType.LAST,
                    last=float(close) if close not in (None, "NA") else None,
                    volume_cum=int(r["volume"]) if r.get("volume") not in (None, "NA") else None,
                    declared_delay_minutes=self.declared_delay_minutes,
                )
            )
        return out

    def supports_streaming(self) -> bool:
        return True


def eodhd_from(config) -> EodhdProvider:  # type: ignore[no-untyped-def]
    """Provider configured from ``params.yaml › data`` (stale thresholds, quota) — never the defaults."""
    d = config.params.data
    return EodhdProvider(
        stale_after=dict(d.stale_after_minutes), daily_quota=int((d.eodhd or {}).get("daily_quota", 100_000))
    )


class EodhdWebSocket:
    """``ws/eu`` client: ≤ 50 symbols per connection, auto-reconnect, gaps marked via aggregator."""

    def __init__(self, api_token: str | None = None, symbols_per_connection: int = 50) -> None:
        self.token = api_token or os.environ.get("EODHD_API_TOKEN", "")
        self.per_conn = symbols_per_connection
        self.aggregator = BarAggregator(source="eodhd_ws", perimeter=MarketPerimeter.CBOE_CONSOLIDATED)
        self.events: list[dict[str, object]] = []  # connection log for qualification (A0.7)
        self.on_bar: Callable[[IntradayBar], None] | None = None
        self.on_tick: Callable[[Tick], None] | None = None

    @staticmethod
    def parse_message(raw: str, received_at: datetime) -> Tick | None:
        msg = json.loads(raw)
        if "s" not in msg or "p" not in msg:
            return None  # status / heartbeat messages
        ts = msg.get("t")
        mts = datetime.fromtimestamp(int(ts) / 1000, tz=UTC) if ts else received_at
        return Tick(
            symbol=str(msg["s"]),
            market_timestamp=mts,
            price=float(msg["p"]),
            size=int(msg.get("v") or 0),
            received_at=received_at,
        )

    def handle_raw(self, raw: str, received_at: datetime | None = None) -> IntradayBar | None:
        received_at = received_at or datetime.now(UTC)
        tick = self.parse_message(raw, received_at)
        if tick is None:
            return None
        if self.on_tick:
            self.on_tick(tick)
        bar = self.aggregator.add(tick)
        if bar and self.on_bar:
            self.on_bar(bar)
        return bar

    def _chunks(self, symbols: Sequence[str]) -> list[list[str]]:
        return [list(symbols[i : i + self.per_conn]) for i in range(0, len(symbols), self.per_conn)]

    async def run(self, symbols: Sequence[str], stop: asyncio.Event) -> None:
        await asyncio.gather(*(self._run_conn(c, stop) for c in self._chunks(symbols)))

    async def _run_conn(self, symbols: list[str], stop: asyncio.Event) -> None:
        import websockets

        backoff = 1.0
        while not stop.is_set():
            try:
                async with websockets.connect(f"{WS_URL}?api_token={self.token}", ping_interval=20) as ws:
                    self.events.append(
                        {"at": datetime.now(UTC).isoformat(), "event": "connected", "symbols": len(symbols)}
                    )
                    backoff = 1.0
                    await ws.send(json.dumps({"action": "subscribe", "symbols": ",".join(symbols)}))
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        self.handle_raw(str(raw))
            except Exception as exc:  # noqa: BLE001 — reconnect, mark the gap, never back-fill
                from app.logging_setup import redact

                self.events.append(
                    {"at": datetime.now(UTC).isoformat(), "event": "disconnected", "error": redact(repr(exc))[:200]}
                )
                self.aggregator.mark_gap()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
