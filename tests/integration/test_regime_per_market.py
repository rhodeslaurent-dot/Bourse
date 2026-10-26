"""docs/32 — a regime per venue (user decision of 26/10/2026: « the CAC 40 context blocks every investment,
and the French market alone is too narrow »): the indices are loaded incrementally, each value is gated by
the regime of its own venue, and the letters' published conditions only apply to the venues they cover."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.data.base import ProviderError
from app.data.dto import DataStatus, EodBar, MarketPerimeter
from app.db.models import Instrument, MarketRegimeRow, PriceEod, Proposal
from app.db.session import db_session
from app.domain.conjuncture import market_gate
from app.services import engine
from app.services.indices import index_specs, refresh_indices
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


class FakeEod:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def fetch_eod(self, symbols, start, end):  # type: ignore[no-untyped-def]
        self.calls.append((symbols[0], start))
        if symbols[0] == "OBX.INDX":
            raise ProviderError("EODHD 404 : symbole inconnu")
        now = datetime.now(UTC)
        days = [start + timedelta(days=k) for k in range(min(5, (end - start).days))]
        return [
            EodBar(source="eodhd", market_perimeter=MarketPerimeter.PRIMARY, received_at=now, processed_at=now,
                   data_status=DataStatus.EOD, symbol=symbols[0], day=d, open=100, high=101, low=99, close=100,
                   volume=0)
            for d in days if d.weekday() < 5
        ]  # fmt: skip


def test_indices_load_incrementally_and_report_unknown_codes(db_engine, config):
    specs = {x.market: x for x in index_specs(config)}
    assert specs["EU"].id == "EU0009658202" and specs["XETR"].eodhd == "GDAXI.INDX" and len(specs) >= 18
    p = FakeEod()
    today = date(2026, 10, 23)
    with db_session() as s:
        out = refresh_indices(s, p, config, today, first_day=date(2026, 10, 19))
        assert out["XOSL"][1] and "404" in out["XOSL"][1]  # reported, never replaced by another index
        assert out["XETR"][0] > 0 and s.query(PriceEod).filter(PriceEod.isin == "IDX.GDAXI").count() > 0
        p.calls.clear()
        refresh_indices(s, p, config, today, first_day=date(2026, 10, 19))
        assert [c[0] for c in p.calls] == ["OBX.INDX"]  # up to date: only the unknown code is tried again
        refresh_indices(s, p, config, date(2026, 10, 27), first_day=date(2026, 10, 19))
        assert dict(p.calls)["GDAXI.INDX"] == date(2026, 10, 24)  # incremental: the day after the last stored


def test_letters_conditions_do_not_gate_other_venues():
    assert market_gate(None, "faible", "green").state == "restreint"  # Paris without a recent publication
    g = market_gate(None, "faible", "green", letters_apply=False)
    assert g.state == "ouvert" and "non applicables" in g.reason
    assert market_gate(None, "faible", "red", letters_apply=False).state == "restreint_plus"  # its own red regime


@pytest.mark.parametrize(
    ("mic", "expected"),
    [
        ("XPAR", ("XPAR", "red", "XPAR")),  # Paris: the CAC 40 is red, the entry is gated
        ("XETR", ("XETR", "green", "XETR")),  # the same value in Frankfurt: Frankfurt's green regime applies
        ("XLIS", ("XLIS", "green", "EU")),  # a venue without a regime of its own: the European one, said so
    ],
)
def test_each_value_is_gated_by_the_regime_of_its_own_venue(db_engine, config, mic, expected):
    with db_session() as s:
        _seed(s, breakout_bars())
        s.get(Instrument, ISIN).mic = mic
        for market, light in (("EU", "green"), ("XPAR", "red"), ("XETR", "green")):
            s.add(MarketRegimeRow(day=DAY, ts=NOW, market=market, regime=light))
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        cms = [p.details["contexte_marche"] for p in s.query(Proposal).filter(Proposal.isin == ISIN)]
        assert cms and all((c["marche"], c["regime"], c["regime_de"]) == expected for c in cms)
        assert all((c["portail"] == "restreint_plus") == (expected[1] == "red") for c in cms)


class FakeYahoo:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_eod(self, symbols, start, end):  # type: ignore[no-untyped-def]
        self.calls.append(symbols[0])
        bars = FakeEod().fetch_eod(["X"], max(start, end - timedelta(days=6)), end)
        return [b.model_copy(update={"source": "yfinance", "symbol": symbols[0]}) for b in bars]


def test_the_same_index_comes_from_yahoo_when_eodhd_fails_or_is_stale(db_engine, raw):
    """26/10/2026: FTSE MIB and FTSE 100 absent from the EODHD plan, ISEQ stopped in 2022 — the same index is read
    from Yahoo (source yfinance on each bar); without a Yahoo code, a stale series is reported, never « à jour »."""
    from app.config import validate_params

    raw["regime"]["indices_by_mic"] = {
        "XOSL": {"id": "IDX.OBX", "eodhd": "OBX.INDX", "yahoo": "OBX.OL", "nom": "OBX"},  # EODHD 404
        "XDUB": {"id": "IDX.ISEQ", "eodhd": "ISEQ.INDX", "yahoo": "^ISEQ", "nom": "ISEQ"},  # stale on EODHD
        "XLIS": {"id": "IDX.PSI", "eodhd": "PSI20.INDX", "nom": "PSI"},  # stale, no Yahoo code
    }
    config = validate_params(raw)
    today = date(2026, 10, 23)
    old = date(2022, 10, 6)

    class StaleEod(FakeEod):
        def fetch_eod(self, symbols, start, end):  # type: ignore[no-untyped-def]
            return [] if symbols[0] in ("ISEQ.INDX", "PSI20.INDX") else super().fetch_eod(symbols, start, end)

    with db_session() as s:
        for isin in ("IDX.ISEQ", "IDX.PSI"):
            s.add(PriceEod(isin=isin, day=old, open=1, high=1, low=1, close=1, volume=0, source="eodhd",
                           received_at=NOW, data_status="eod"))  # fmt: skip
        s.flush()
        y = FakeYahoo()
        out = refresh_indices(s, StaleEod(), config, today, first_day=date(2026, 10, 19), fallback=y)
        assert y.calls == ["OBX.OL", "^ISEQ"]  # only where EODHD failed or is stale, and a Yahoo code exists
        assert out["XOSL"][1] is None and out["XDUB"][1] is None and out["XOSL"][0] > 0
        srcs = {r.source for r in s.query(PriceEod).filter(PriceEod.isin == "IDX.OBX")}
        assert srcs == {"yfinance"}  # the provider stays visible on every bar (rule 2)
        assert out["XLIS"][1] is not None and out["XLIS"][1].startswith("périmé : dernière séance le 2022-10-06")


def test_a_stale_index_gives_no_regime_the_venue_follows_europe(db_engine, config, calendars):
    from app.scheduler.jobs.daily_regime import compute_and_store
    from app.scheduler.runner import JobContext

    with db_session() as s:
        _seed(s, breakout_bars())
        s.get(Instrument, ISIN).mic = "XDUB"
        for k in range(60):  # Dublin's index stopped long ago; Europe's is current
            d = DAY - timedelta(days=k)
            s.add(PriceEod(isin="EU0009658202", day=d, open=1, high=1, low=1, close=1, volume=0, source="eodhd",
                           received_at=NOW, data_status="eod"))  # fmt: skip
            s.add(PriceEod(isin="IDX.ISEQ", day=date(2022, 10, 6) - timedelta(days=k), open=1, high=1, low=1,
                           close=1, volume=0, source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
    compute_and_store(JobContext(config, calendars, DAY, ("XPAR",), "normal"))
    with db_session() as s:
        assert {r.market for r in s.query(MarketRegimeRow)} == {"EU"}  # never a « today » regime from 2022 closes
