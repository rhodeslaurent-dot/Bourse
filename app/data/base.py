"""Provider interface (docs/03 A6, docs/04 §4.5). Every implementation is read-only."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date

from app.data.dto import EodBar, IntradayBar, Quote, Timeframe


class ProviderError(RuntimeError):
    pass


class QuotaExceeded(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class MarketDataProvider(ABC):
    name: str
    declared_delay_minutes: int  # 0 = realtime (as declared by the vendor; verified by A0.7)

    @abstractmethod
    def fetch_eod(self, symbols: Sequence[str], start: date, end: date) -> list[EodBar]: ...

    @abstractmethod
    def fetch_intraday(self, symbol: str, day: date, tf: Timeframe = Timeframe.M5) -> list[IntradayBar]: ...

    @abstractmethod
    def fetch_quotes(self, symbols: Sequence[str]) -> list[Quote]: ...

    def supports_streaming(self) -> bool:
        return False
