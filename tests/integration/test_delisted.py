"""docs/33 §33.3 — delisted shares for research registration n° 2: main venues only, a generous liquidity screen
on monthly whole-exchange snapshots (no history read), inactive instruments, then their history."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.data.dto import DataStatus, EodBar, MarketPerimeter
from app.db.models import FxRate, Instrument, InstrumentAlias, PriceEod
from app.db.session import db_session
from app.domain.research.report import ISIN_RE
from app.services import delisted

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def _bar(sym: str, d: date, close: float, volume: int) -> EodBar:
    return EodBar(source="eodhd", market_perimeter=MarketPerimeter.PRIMARY, received_at=NOW, processed_at=NOW,
                  data_status=DataStatus.EOD, symbol=sym, day=d, open=close, high=close, low=close, close=close,
                  volume=volume)  # fmt: skip


class FakeBulk:
    def __init__(self) -> None:
        self.calls: list[date] = []

    def fetch_eod_bulk(self, exchange, day):  # type: ignore[no-untyped-def]
        self.calls.append(day)
        if day == date(2018, 1, 1):  # New Year: closed, the next weekday answers
            return []
        return [_bar("BIG.US", day, 50.0, 400_000), _bar("TINY.US", day, 2.0, 1_000), _bar("LIVE.US", day, 9.0, 9)]

    def fetch_eod(self, symbols, start, end):  # type: ignore[no-untyped-def]
        return [_bar(symbols[0], date(2018, 1, 2), 50.0, 400_000), _bar(symbols[0], date(2018, 1, 3), 51.0, 1)]


ROWS = [
    {"Code": "BIG", "Name": "Big Corp", "Exchange": "NYSE", "Currency": "USD", "Isin": "US0000000001"},
    {"Code": "TINY", "Name": "Tiny", "Exchange": "NASDAQ", "Currency": "USD", "Isin": None},
    {"Code": "OTCX", "Name": "Pink", "Exchange": "PINK", "Currency": "USD", "Isin": None},
]


def test_main_venues_months_and_pseudo_keys():
    assert [r["Code"] for r in delisted.main_venue_rows(ROWS, "US")] == ["BIG", "TINY"]  # OTC dropped
    assert len(delisted.main_venue_rows(ROWS, "LSE")) == 3  # no filter where the code is a single venue
    assert delisted.month_starts(date(2018, 1, 1), date(2018, 4, 15)) == [
        date(2018, 1, 1), date(2018, 2, 1), date(2018, 3, 1), date(2018, 4, 2)]  # fmt: skip
    k = delisted.pseudo_id("TINY", "US")
    assert len(k) == 12 and k == delisted.pseudo_id("TINY", "US") and not ISIN_RE.search(k)


def test_screen_keeps_liquid_delisted_shares_and_stores_them_inactive(db_engine, config):
    with db_session() as s:
        s.add(FxRate(pair="EURUSD", day=date(2017, 12, 29), rate=1.2, source="ecb"))
        s.flush()
        rows = delisted.main_venue_rows(ROWS, "US")
        p = FakeBulk()
        floor = delisted.min_turnover_eur(config, "US")  # market_overrides US: 20 M€
        rep = delisted.screen(s, p, "US", rows, [date(2018, 1, 1), date(2018, 2, 1)], floor)
        assert date(2018, 1, 2) in p.calls and rep.snapshots == 2 and rep.seen == 2
        assert floor == 20_000_000 and set(rep.kept) == {"BIG"}  # 16.7 M€ ≥ half the floor ; TINY 1 667 €
        keys = delisted.store(s, rows, {"BIG", "TINY"}, "US", "XNYS")
        assert keys == ["US0000000001", delisted.pseudo_id("TINY", "US")]
        inst = s.get(Instrument, "US0000000001")
        assert (inst.active, inst.in_universe, inst.ticker_eodhd, inst.mic) == (False, False, "BIG.US", "XNYS")
        assert s.query(InstrumentAlias).filter(InstrumentAlias.alias == "TINY.US").one().isin == keys[1]
        assert delisted.store(s, rows, {"BIG"}, "US", "XNYS") == ["US0000000001"]  # idempotent
        assert s.query(Instrument).count() == 2
        s.commit()

    from app.scheduler.jobs.eod_backfill_check import backfill_history

    assert backfill_history(FakeBulk(), date(2018, 1, 1), date(2018, 2, 1), keys) == 0  # inactive: skipped
    assert backfill_history(FakeBulk(), date(2018, 1, 1), date(2018, 2, 1), keys, include_inactive=True) == 4
    with db_session() as s:
        assert s.query(PriceEod).filter(PriceEod.isin == keys[1]).count() == 2
