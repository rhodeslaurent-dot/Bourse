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
