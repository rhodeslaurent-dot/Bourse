"""yfinance — EOD fallback only (docs/04 §4.1: unofficial, 429 episodes). Status ``eod`` and
``source=yfinance`` so that a degraded source is always visible."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime

from app.data.base import MarketDataProvider, ProviderError, ProviderUnavailable
from app.data.dto import DataStatus, EodBar, IntradayBar, MarketPerimeter, PriceType, Quote, Timeframe

log = logging.getLogger("bourse.data.yfinance")


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"
    declared_delay_minutes = 15

    def __init__(self, downloader=None) -> None:  # type: ignore[no-untyped-def]
        self._download = downloader

    def _dl(self):  # type: ignore[no-untyped-def]
        if self._download is not None:
            return self._download
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise ProviderUnavailable("yfinance non installé") from exc
        return yf.download

    def fetch_eod(self, symbols: Sequence[str], start: date, end: date) -> list[EodBar]:
        out: list[EodBar] = []
        now = datetime.now(UTC)
        for sym in symbols:
            try:
                df = self._dl()(sym, start=start.isoformat(), end=end.isoformat(), progress=False, auto_adjust=False)
            except Exception as exc:  # noqa: BLE001
                raise ProviderUnavailable(f"yfinance {sym}: {exc}") from exc
            if df is None or len(df) == 0:
                continue
            cols = {str(c[0] if isinstance(c, tuple) else c).lower(): c for c in df.columns}
            for idx, row in df.iterrows():
                if row[cols["close"]] != row[cols["close"]]:  # NaN
                    continue
                out.append(
                    EodBar(
                        source=self.name,
                        market_perimeter=MarketPerimeter.PRIMARY,
                        received_at=now,
                        processed_at=datetime.now(UTC),
                        data_status=DataStatus.EOD,
                        symbol=sym,
                        day=idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10]),
                        open=float(row[cols["open"]]),
                        high=float(row[cols["high"]]),
                        low=float(row[cols["low"]]),
                        close=float(row[cols["close"]]),
                        adj_close=float(row[cols["adj close"]]) if "adj close" in cols else None,
                        volume=int(row[cols["volume"]]) if row[cols["volume"]] == row[cols["volume"]] else 0,
                    )
                )
        return out

    def fetch_intraday(self, symbol: str, day: date, tf: Timeframe = Timeframe.M5) -> list[IntradayBar]:
        raise ProviderError("yfinance n'est pas retenu pour l'intraday (secours EOD uniquement)")

    def fetch_quotes(self, symbols: Sequence[str]) -> list[Quote]:
        """Last close as an EOD quote — never presented as a live price."""
        bars = self.fetch_eod(symbols, date.today().replace(day=1), date.today())
        latest: dict[str, EodBar] = {}
        for b in bars:
            if b.symbol not in latest or b.day > latest[b.symbol].day:
                latest[b.symbol] = b
        return [
            Quote(
                source=self.name,
                market_perimeter=MarketPerimeter.PRIMARY,
                received_at=b.received_at,
                processed_at=datetime.now(UTC),
                data_status=DataStatus.EOD,
                symbol=b.symbol,
                market_timestamp=datetime.combine(b.day, datetime.min.time(), tzinfo=UTC),
                price_type=PriceType.CLOSE,
                last=b.close,
                volume_cum=b.volume,
                declared_delay_minutes=self.declared_delay_minutes,
            )
            for b in latest.values()
        ]
