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
    p0_p1_p2 = {"PA", "AS", "BR", "XETRA", "MI", "MC", "ST", "CO", "HE", "OL", "LS", "IR", "VI", "WAR"}
    assert set(fake.calls) == p0_p1_p2  # toutes les places PEA ont un calendrier depuis le 22/10/2026
