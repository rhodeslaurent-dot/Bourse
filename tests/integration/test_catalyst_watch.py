"""18/11/2026 — the watchlist's catalyst entry: results welcomed by the market, the radar's attention; facts only,
watching only, values of the universe, another reason left as it is, expiry."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db.models import Instrument, OfficialFiling, PriceEod, WatchlistRow, WeakSignalHit
from app.db.session import db_session
from app.services import catalyst_watch

ISIN, OTHER = "FR0000000CA1", "FR0000000CA2"
DAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 6, 15, tzinfo=UTC)


def _seed(s, isin: str, jump: float = 1.06, vol: float = 3000) -> None:
    s.add(Instrument(isin=isin, name=f"Société {isin[-1]}", mic="XPAR", currency="EUR", in_universe=True))
    d = date(2026, 10, 15)
    while d <= date(2026, 11, 18):
        if d.weekday() < 5:
            last = d == date(2026, 11, 18)
            c = 80.0 * (jump if last else 1.0)  # a value down for months: never in the momentum list
            s.add(PriceEod(isin=isin, day=d, open=c, high=c, low=c, close=c, volume=vol if last else 1000,
                           source="eodhd", received_at=NOW))  # fmt: skip
        d += timedelta(days=1)
    s.add(OfficialFiling(source="info_financiere", diffuser="AMF", uin_id=f"u-{isin}", isin=isin, family="resultats",
                         title="Résultats du troisième trimestre",
                         market_timestamp=datetime(2026, 11, 17, 16, 45, tzinfo=UTC),  # 17:45 Paris: after the close
                         time_source="amf", time_precision="seconde", received_at=NOW, processed_at=NOW,
                         data_status="eod"))  # fmt: skip


def test_results_welcomed_by_the_market_enter_the_watchlist(db_engine, config, calendars):
    with db_session() as s:
        _seed(s, ISIN)
        _seed(s, OTHER, jump=1.01)  # results, but no clear reaction: not added
        added, gone = catalyst_watch.refresh(s, config, calendars, DAY, NOW)
        rows = list(s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True))))
    assert added == 1 and gone == 0 and [r.isin for r in rows] == [ISIN]
    (r,) = rows
    assert r.reason == "catalyseur" and r.source == "resultats"
    assert r.levels["fait"] == (
        "résultats publiés le 17/11, salués par le marché : +6,0% le 18/11 avec 3,0 fois le volume habituel"
    )
    assert r.levels["expire_le"] == "2026-12-03"


def test_a_value_already_watched_is_left_and_a_catalyst_expires(db_engine, config, calendars):
    with db_session() as s:
        _seed(s, ISIN)
        s.add(WatchlistRow(isin=ISIN, reason="manual", source="manual", active=True))
        s.add(Instrument(isin=OTHER, name="Radar", mic="XPAR", currency="EUR", in_universe=True))
        s.add(WeakSignalHit(isin=OTHER, day=date(2026, 11, 18), label="attention_corroboree", text="attention",
                            computed_at=NOW))  # fmt: skip
        s.flush()
        added, _ = catalyst_watch.refresh(s, config, calendars, DAY, NOW)
        rows = {r.isin: r for r in s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True)))}
        assert added == 1 and rows[ISIN].reason == "manual"  # never replaced
        assert rows[OTHER].source == "radar" and "attention inhabituelle" in rows[OTHER].levels["fait"]
        _, gone = catalyst_watch.refresh(s, config, calendars, DAY + timedelta(days=30), NOW + timedelta(days=30))
        assert gone == 1 and not rows[OTHER].active


def test_without_the_group_nothing_changes(db_engine, config, calendars):
    config.raw.pop("watchlist_catalyst")
    with db_session() as s:
        _seed(s, ISIN)
        assert catalyst_watch.refresh(s, config, calendars, DAY, NOW) == (0, 0)


def test_the_watchlist_page_says_the_fact_and_that_it_is_never_an_order(db_engine, config, calendars):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with db_session() as s:
        _seed(s, ISIN)
        catalyst_watch.refresh(s, config, calendars, DAY, NOW)
    app = create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/watchlist").text
    assert "Catalyseur (résultats salués, actualité)" in html
    assert "salués par le marché : +6,0% le 18/11 avec 3,0 fois le volume habituel" in html
    assert "À suivre de près" in html and "signal d&#39;achat en soi" in html
