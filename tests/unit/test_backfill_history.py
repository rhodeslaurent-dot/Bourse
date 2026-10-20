"""``backfill`` keeps going when EODHD refuses one symbol and stops cleanly on quota exhaustion."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app.data.base import ProviderError, QuotaExceeded
from app.data.dto import DataStatus, EodBar, MarketPerimeter
from app.db.models import Instrument, PriceEod
from app.db.session import db_session
from app.scheduler.jobs.eod_backfill_check import backfill_history


def _bar(symbol: str, d: date) -> EodBar:
    ts = datetime(d.year, d.month, d.day, 15, 30, tzinfo=UTC)
    return EodBar(
        source="eodhd",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=ts,
        processed_at=ts,
        symbol=symbol,
        day=d,
        open=1.0,
        high=1.2,
        low=0.9,
        close=1.1,
        volume=1000,
        data_status=DataStatus.EOD,
    )


class FakeProvider:
    def __init__(self, quota_after: int | None = None) -> None:
        self.calls = 0
        self.quota_after = quota_after

    def fetch_eod(self, symbols, start, end):
        self.calls += 1
        if self.quota_after is not None and self.calls > self.quota_after:
            raise QuotaExceeded("quota EODHD journalier atteint")
        if symbols[0] == "DEAD.PA":
            raise ProviderError("EODHD 404 on /eod/DEAD.PA: Symbol not found")
        return [_bar(symbols[0], date(2026, 10, 19)), _bar(symbols[0], date(2026, 10, 20))]


def _seed():
    with db_session() as s:
        for code, isin in (("TTE", "FR0000120271"), ("DEAD", "FR0000000001"), ("AI", "FR0000120073")):
            s.add(Instrument(isin=isin, name=code, ticker_local=code, ticker_eodhd=f"{code}.PA", mic="XPAR"))


def test_refused_symbol_is_skipped(db_engine):
    _seed()
    n = backfill_history(FakeProvider(), date(2026, 10, 1), date(2026, 10, 20))  # type: ignore[arg-type]
    assert n == 4
    with db_session() as s:
        assert s.scalar(select(func.count(PriceEod.id))) == 4
        assert s.scalar(select(func.count(PriceEod.id)).where(PriceEod.isin == "FR0000000001")) == 0


def test_quota_exhaustion_keeps_what_was_stored(db_engine):
    _seed()
    n = backfill_history(FakeProvider(quota_after=1), date(2026, 10, 1), date(2026, 10, 20))  # type: ignore[arg-type]
    assert n == 2
    with db_session() as s:
        assert s.scalar(select(func.count(PriceEod.id))) == 2
