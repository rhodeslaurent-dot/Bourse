"""TradingView screener (delayed ~15 min, unofficial endpoint) — docs/04 §4.5.

Direct call to ``scanner.tradingview.com/{market}/scan`` (same request format as the MIT
``tradingview-screener`` package, without its ``requests`` dependency so that tests can record
responses with respx). Never indispensable: ≤ 6 requests per scan, ≥ 60 s between scans,
disable-able by parameter; fallback = EODHD REST delayed.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime

import httpx

from app.data.base import ProviderError
from app.data.dto import ScreenerRow

log = logging.getLogger("bourse.data.tradingview")
BASE = "https://scanner.tradingview.com"
DEFAULT_COLUMNS = [
    "name", "close", "open", "gap", "change", "change_from_open", "volume", "relative_volume_10d_calc",
    "average_volume_10d_calc", "market_cap_basic", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.Y", "SMA20", "SMA50",
    "SMA200", "ATR", "High.All", "price_52_week_high", "sector", "earnings_release_next_date",
]  # fmt: skip
MARKETS_BY_EODHD = {
    "PA": "france",
    "XETRA": "germany",
    "AS": "netherlands",
    "BR": "belgium",
    "MI": "italy",
    "MC": "spain",
}


class TradingViewScreener:
    name = "tradingview_screener"
    declared_delay_minutes = 15

    def __init__(
        self,
        client: httpx.Client | None = None,
        min_interval_seconds: int = 60,
        max_requests_per_scan: int = 6,
        limit_per_request: int = 1500,
        enabled: bool = True,
    ) -> None:
        self.client = client or httpx.Client(
            base_url=BASE, timeout=30, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        self.min_interval = min_interval_seconds
        self.max_requests = max_requests_per_scan
        self.limit = limit_per_request
        self.enabled = enabled
        self._last_scan_at = 0.0
        self.requests_made = 0

    def scan(
        self, markets: list[str], columns: list[str] | None = None, filters: list[dict] | None = None
    ) -> list[ScreenerRow]:
        if not self.enabled:
            raise ProviderError("tradingview_screener désactivé par paramètre")
        if len(markets) > self.max_requests:
            raise ProviderError(f"{len(markets)} marchés > max_requests_per_scan={self.max_requests}")
        since = time.monotonic() - self._last_scan_at
        if self._last_scan_at and since < self.min_interval:
            raise ProviderError(f"scan trop rapproché ({since:.0f}s < {self.min_interval}s)")
        self._last_scan_at = time.monotonic()
        cols = columns or DEFAULT_COLUMNS
        rows: list[ScreenerRow] = []
        for market in markets:
            payload = {
                "columns": cols,
                "filter": filters or [{"left": "type", "operation": "equal", "right": "stock"}],
                "range": [0, self.limit],
                "sort": {"sortBy": "volume", "sortOrder": "desc"},
                "markets": [market],
            }
            self.requests_made += 1
            r = self.client.post(f"/{market}/scan", json=payload)
            if r.status_code >= 400:
                raise ProviderError(f"tradingview {r.status_code} on {market}: {r.text[:120]}")
            received = datetime.now(UTC)
            for item in r.json().get("data", []):
                rows.append(self._row(item["s"], dict(zip(cols, item["d"], strict=False)), received))
        return rows

    @staticmethod
    def _row(ticker: str, d: dict, received: datetime) -> ScreenerRow:
        def f(k: str) -> float | None:
            v = d.get(k)
            return float(v) if isinstance(v, int | float) else None

        erd = d.get("earnings_release_next_date")
        erd_date = datetime.fromtimestamp(int(erd), tz=UTC).date() if isinstance(erd, int | float) and erd else None
        return ScreenerRow(
            ticker=ticker,
            name=d.get("name"),
            close=f("close"),
            open=f("open"),
            volume=f("volume"),
            change_pct=f("change"),
            gap_pct=f("gap"),
            relative_volume_10d=f("relative_volume_10d_calc"),
            average_volume_10d=f("average_volume_10d_calc"),
            market_cap=f("market_cap_basic"),
            sector=d.get("sector") if isinstance(d.get("sector"), str) else None,
            earnings_release_next_date=erd_date,
            extra={k: (v if isinstance(v, int | float | str) or v is None else str(v)) for k, v in d.items()},
            received_at=received,
        )


def parse_earnings(value: object) -> date | None:
    if isinstance(value, int | float) and value:
        return datetime.fromtimestamp(int(value), tz=UTC).date()
    return None
