"""universe_refresh: a market without a calendar is not loaded (ADR-005 M7) and an exchange code that
EODHD rejects is skipped with a reason instead of aborting the whole refresh."""

from __future__ import annotations

from app.data.base import ProviderError
from app.scheduler.jobs.universe_refresh import symbol_rows_from_eodhd


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def exchange_symbols(self, exchange: str, types=None):
        self.calls.append(exchange)
        if exchange == "MI":
            raise ProviderError("EODHD 404 on /exchange-symbol-list/MI: Exchange Not Found.")
        return [
            {"Code": "TTE", "Name": "TotalEnergies", "Isin": "FR0000120271", "Currency": "EUR", "Country": "France"},
            {"Code": "X", "Name": "sans isin", "Isin": "", "Currency": "EUR", "Country": "France"},
        ]

    def exchanges(self):
        return [{"Code": "MI", "Name": "Borsa Italiana", "Country": "Italy", "Currency": "EUR"}]


def test_failed_exchange_is_recorded_and_skipped():
    errors: dict[str, str] = {}
    rows = symbol_rows_from_eodhd(FakeProvider(), ["PA", "MI", "AS"], errors)  # type: ignore[arg-type]
    assert [r.exchange for r in rows] == ["PA", "AS"] and list(errors) == ["MI"] and "404" in errors["MI"]


def test_without_error_sink_the_exception_propagates():
    import pytest

    with pytest.raises(ProviderError):
        symbol_rows_from_eodhd(FakeProvider(), ["MI"])  # type: ignore[arg-type]


def test_markets_without_calendar_are_not_requested(config, calendars, monkeypatch, db_engine):
    from datetime import date

    from app.scheduler.jobs import universe_refresh as job
    from app.scheduler.runner import JobContext

    fake = FakeProvider()
    monkeypatch.setenv("EODHD_API_TOKEN", "t")
    monkeypatch.setattr("app.data.providers.eodhd.eodhd_from", lambda cfg: fake)
    monkeypatch.setattr(job, "enrich_with_screener", lambda rows, cfg, fx=None: rows)
    ctx = JobContext(config, calendars, date(2026, 10, 20), ("XPAR",), "normal")
    assert job.run(ctx) >= 0
    p0_p1_p2 = {"PA", "AS", "BR", "XETRA", "MC", "ST", "CO", "HE", "OL", "LS", "IR", "VI", "WAR"}
    assert set(fake.calls) == p0_p1_p2  # toutes les places PEA ont un calendrier depuis le 22/10/2026


def test_new_listings_get_their_history_so_they_can_enter_the_universe(config, db_engine):
    """22/10/2026: Milan, Madrid… calendars enabled, but a listing without 250 sessions of history
    was excluded and nothing fetched it — only the four P0 places reached the universe (393 values)."""
    from datetime import UTC, date, datetime, timedelta

    from app.data.base import QuotaExceeded
    from app.data.dto import DataStatus, EodBar, MarketPerimeter
    from app.db.models import PriceEod
    from app.db.session import db_session
    from app.scheduler.jobs.universe_refresh import backfill_new_listings
    from app.services.universe import SymbolRow

    today = date(2026, 10, 23)
    now = datetime.now(UTC)

    class Eod:
        def __init__(self):
            self.calls: list[str] = []

        def fetch_eod(self, symbols, start, end):
            self.calls.append(symbols[0])
            if symbols[0].startswith("QUOTA"):
                raise QuotaExceeded("EODHD 402")
            return [
                EodBar(source="eodhd", market_perimeter=MarketPerimeter.PRIMARY, received_at=now, processed_at=now,
                       data_status=DataStatus.EOD, symbol=symbols[0], day=start + timedelta(days=k),
                       open=10, high=10.5, low=9.5, close=10, volume=1000)
                for k in range(3)
            ]  # fmt: skip

    rows = [
        SymbolRow(isin="IT0003128367", name="Enel", code="ENEL", exchange="MI", currency="EUR", market_cap_eur=7e10),
        SymbolRow(isin="IT0000000001", name="Tiny", code="TINY", exchange="MI", currency="EUR", market_cap_eur=5e7),
        SymbolRow(isin="IT0000000002", name="NoCap", code="NOCAP", exchange="MI", currency="EUR"),
        SymbolRow(
            isin="IE0000000003", name="Fund", code="ETF", exchange="MI", currency="EUR", market_cap_eur=9e9, kind="etf"
        ),  # fmt: skip
        SymbolRow(isin="IT0000000004", name="Q", code="QUOTA", exchange="MI", currency="EUR", market_cap_eur=9e9),
        SymbolRow(isin="IT0000000005", name="After", code="AFTER", exchange="MI", currency="EUR", market_cap_eur=9e9),
    ]
    p = Eod()
    assert backfill_new_listings(p, rows, config, today) == 3  # cap known ≥ floor, stocks only
    assert p.calls == ["ENEL.MI", "QUOTA.MI"]  # quota: stops cleanly, the rest waits for the next run
    with db_session() as s:
        assert s.query(PriceEod).filter(PriceEod.isin == "IT0003128367").count() == 3


def test_a_missing_price_is_reported_as_unknown_not_as_a_low_price():
    from app.domain.universe import InstrumentFacts, universe_decision
    from app.services.universe import UniverseFilters

    filt = UniverseFilters(1e6, 5e5, 3e8, 1e9, 1.0, 250, [])
    dec = universe_decision(InstrumentFacts("SE0000115446", "XSTO", None, 4e10, None, 0), filt)
    assert (
        "cours inconnu (aucun cours stocké)" in dec.reasons
        and "ADV20 inconnue (moins de 20 séances stockées)" in dec.reasons
    )
    assert not any(r.startswith("prix <") or r.startswith("ADV20 <") for r in dec.reasons)
