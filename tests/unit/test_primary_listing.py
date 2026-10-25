"""25/10/2026 — Paris large caps were stored under their Xetra cross-listing (AIL.XETRA, TOTB.XETRA): each
symbol row overwrote the instrument of the same ISIN, and Xetra comes after Paris in the configured order.
One listing per ISIN is now followed, the primary one, and the old venue's history is never kept."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.domain.universe import primary_exchange
from app.services.universe import SymbolRow, primary_listings


def _row(isin: str, code: str, ex: str, **kw) -> SymbolRow:  # type: ignore[no-untyped-def]
    return SymbolRow(isin=isin, name=code, code=code, exchange=ex, currency="EUR", **kw)


def test_home_exchange_from_the_isin_then_first_venue_that_is_not_xetra():
    assert primary_exchange("FR0000120073", ["PA", "XETRA"]) == "PA"
    assert primary_exchange("FR0000120073", ["XETRA", "PA"]) == "PA"
    assert primary_exchange("DE0007164600", ["PA", "XETRA"]) == "XETRA"  # SAP: Xetra is its home
    assert primary_exchange("NL0000235190", ["XETRA", "PA", "MC"]) == "PA"  # Airbus: not on AS → first non-Xetra
    assert primary_exchange("US0378331005", ["XETRA"]) == "XETRA"  # only listing followed
    assert primary_exchange("NL0000235190", ["PA", "MC"], {"NL0000235190": "MC"}) == "MC"  # user override
    assert primary_exchange("NL0000235190", ["PA"], {"NL0000235190": "AS"}) == "PA"  # override not followed


def test_one_row_per_isin_keeps_the_primary_tickers_and_borrows_issuer_facts():
    rows = [
        _row("FR0000120271", "TTE", "PA", tv_ticker="EURONEXT:TTE"),
        _row("FR0000120073", "AI", "PA"),
        _row("DE0007164600", "SAP", "XETRA", market_cap_eur=2e11),
        _row("FR0000120271", "TOTB", "XETRA", market_cap_eur=1.2e11, sector="Energy", tv_ticker="XETR:TOTB"),
        _row("FR0000120073", "AIL", "XETRA"),
    ]
    out = primary_listings(rows)
    assert [(r.isin, r.code, r.exchange) for r in out] == [
        ("FR0000120271", "TTE", "PA"),
        ("FR0000120073", "AI", "PA"),
        ("DE0007164600", "SAP", "XETRA"),
    ]
    tte = out[0]
    assert tte.market_cap_eur == 1.2e11 and tte.sector == "Energy"  # issuer-level facts
    assert tte.tv_ticker == "EURONEXT:TTE" and tte.extra["autres_cotations"] == ["TOTB.XETRA"]


def test_refresh_never_lets_a_secondary_listing_overwrite_the_primary(config, calendars, db_engine):
    from app.db.models import Instrument
    from app.db.session import db_session
    from app.services.universe import refresh_universe

    rows = [_row("FR0000120073", "AI", "PA"), _row("FR0000120073", "AIL", "XETRA")]
    with db_session() as s:
        rep = refresh_universe(s, config, calendars, rows, date(2026, 10, 23))
        inst = s.get(Instrument, "FR0000120073")
        assert rep.total == 1
        assert (inst.ticker_eodhd, inst.mic, inst.ticker_local) == ("AI.PA", "XPAR", "AI")


def test_relisted_instrument_history_is_replaced_never_mixed(config, calendars, db_engine):
    from app.data.base import ProviderError
    from app.data.dto import DataStatus, EodBar, MarketPerimeter
    from app.db.models import FeatureDaily, Instrument, PriceEod
    from app.db.session import db_session
    from app.scheduler.jobs.universe_refresh import repair_relisted_history
    from app.services.market_data import upsert_eod
    from app.services.universe import refresh_universe

    now = datetime.now(UTC)
    today = date(2026, 10, 23)
    d0 = date(2026, 10, 12)

    def bars(symbol: str, days: list[date], close: float, volume: int) -> list[EodBar]:
        return [
            EodBar(source="eodhd", market_perimeter=MarketPerimeter.PRIMARY, received_at=now, processed_at=now,
                   data_status=DataStatus.EOD, symbol=symbol, day=d, open=close, high=close, low=close,
                   close=close, volume=volume)
            for d in days
        ]  # fmt: skip

    xetra_days = [d0 + timedelta(days=k) for k in range(5)]
    with db_session() as s:  # state before the fix: both ISINs followed on Xetra, with Xetra prices
        refresh_universe(s, config, calendars, [_row("FR0000120073", "AIL", "XETRA")], today)
        refresh_universe(s, config, calendars, [_row("FR0000120271", "TOTB", "XETRA")], today)
        upsert_eod(s, bars("AIL.XETRA", xetra_days, 170.0, 300), {"AIL.XETRA": "FR0000120073"})
        upsert_eod(s, bars("TOTB.XETRA", xetra_days, 55.0, 200), {"TOTB.XETRA": "FR0000120271"})
        s.add(FeatureDaily(isin="FR0000120073", day=xetra_days[-1]))
        s.get(Instrument, "FR0000120271").ticker_tv = "XETR:TOTB"

    class Eod:
        def __init__(self) -> None:
            self.calls: list[tuple[str, date]] = []

        def fetch_eod(self, symbols, start, end):  # type: ignore[no-untyped-def]
            self.calls.append((symbols[0], start))
            if symbols[0] == "TTE.PA":
                raise ProviderError("EODHD 500")
            return bars(symbols[0], xetra_days[1:], 171.0, 450_000)  # Paris: one session fewer, real volume

    rows = primary_listings(
        [_row("FR0000120073", "AI", "PA"), _row("FR0000120271", "TTE", "PA"), _row("FR0000120073", "AIL", "XETRA")]
    )
    p = Eod()
    assert repair_relisted_history(p, rows, today) == (2, 1)
    assert p.calls == [("AI.PA", d0), ("TTE.PA", d0)]  # reloaded over the span already stored
    with db_session() as s:
        ai = s.query(PriceEod).filter(PriceEod.isin == "FR0000120073").order_by(PriceEod.day).all()
        assert [b.day for b in ai] == xetra_days[1:]  # the Xetra-only day is gone
        assert {(b.close, b.volume) for b in ai} == {(171.0, 450_000)}
        assert s.query(FeatureDaily).filter(FeatureDaily.isin == "FR0000120073").count() == 0
        assert s.query(PriceEod).filter(PriceEod.isin == "FR0000120271").count() == 0  # failed: empty, not mixed
        refresh_universe(s, config, calendars, rows, today)
        tte = s.get(Instrument, "FR0000120271")
        assert (tte.ticker_eodhd, tte.mic, tte.ticker_tv) == ("TTE.PA", "XPAR", None)
    assert repair_relisted_history(Eod(), rows, today) == (0, 0)  # once fixed, nothing more to do
