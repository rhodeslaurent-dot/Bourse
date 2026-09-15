"""A1.5 (mechanics on fixtures): referential + universe snapshot/diff, PEA/SRD flags, EOD upsert
and gap detection *per venue*, features with RS rank, momentum watchlist, regime job, S7 alert."""

import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.data.dto import DataStatus, EodBar, MarketPerimeter
from app.db.models import Instrument, MarketRegimeRow, PriceEod, UniverseSnapshot, WatchlistRow
from app.db.session import db_session
from app.scheduler.jobs.daily_regime import INDEX_ISINS, compute_and_store
from app.scheduler.runner import JobContext
from app.services.market_data import compute_features_for_day, detect_gaps, latest_features, upsert_eod, upsert_fx
from app.services.universe import SymbolRow, refresh_universe, tradingview_export

FX = Path(__file__).resolve().parents[1] / "fixtures" / "ohlc"
DAY = date(2026, 9, 14)


def _bars_from_fixture(name: str, symbol: str, end: date, sessions_calendar) -> list[EodBar]:
    with open(FX / name, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    days = sessions_calendar.sessions_between(end - timedelta(days=520), end)[-len(rows) :]
    now = datetime.now(UTC)
    return [
        EodBar(
            source="eodhd",
            market_perimeter=MarketPerimeter.PRIMARY,
            received_at=now,
            processed_at=now,
            data_status=DataStatus.EOD,
            symbol=symbol,
            day=d,
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=int(r["volume"]),
        )
        for d, r in zip(days, rows, strict=False)
    ]


@pytest.fixture
def seeded(db_engine, calendars):
    xpar, xetr = calendars.get("XPAR"), calendars.get("XETR")
    with db_session() as s:
        n = upsert_eod(s, _bars_from_fixture("uptrend_pullback.csv", "TTE.PA", DAY, xpar), {"TTE.PA": "FR0000120271"})
        upsert_eod(s, _bars_from_fixture("flat.csv", "AI.PA", DAY, xpar), {"AI.PA": "FR0000120073"})
        upsert_eod(s, _bars_from_fixture("uptrend_pullback.csv", "SAP.XETRA", DAY, xetr), {"SAP.XETRA": "DE0007164600"})
        upsert_eod(
            s, _bars_from_fixture("uptrend_pullback.csv", "CAC.INDX", DAY, xpar), {"CAC.INDX": INDEX_ISINS["cac40"]}
        )
        upsert_eod(s, _bars_from_fixture("flat.csv", "STOXX.INDX", DAY, xpar), {"STOXX.INDX": INDEX_ISINS["stoxx600"]})
        upsert_fx(s, "EURSEK", DAY, 11.0, "test")
    return n


def test_eod_upsert_official_replaces_provisional(db_engine):
    now = datetime.now(UTC)
    prov = EodBar(
        source="tradingview_screener",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=now,
        processed_at=now,
        data_status=DataStatus.DELAYED,
        symbol="TTE.PA",
        day=DAY,
        open=1,
        high=2,
        low=0.5,
        close=1.5,
        volume=10,
        official=False,
    )
    off = EodBar(
        source="eodhd",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=now,
        processed_at=now,
        data_status=DataStatus.EOD,
        symbol="TTE.PA",
        day=DAY,
        open=1,
        high=2,
        low=0.5,
        close=1.6,
        volume=12,
        official=True,
    )
    with db_session() as s:
        assert upsert_eod(s, [prov], {"TTE.PA": "X"}) == 1
        assert upsert_eod(s, [off], {"TTE.PA": "X"}) == 1
        assert upsert_eod(s, [prov], {"TTE.PA": "X"}) == 0  # provisional never overwrites official
        row = s.scalar(select(PriceEod).where(PriceEod.isin == "X"))
        assert row.official and row.close == 1.6 and row.source == "eodhd"


def test_gap_detection_uses_venue_calendar(seeded, calendars):
    with db_session() as s:
        # remove one Paris session and check the gap is found for XPAR only on real sessions
        row = s.scalar(select(PriceEod).where(PriceEod.isin == "FR0000120271", PriceEod.day == date(2026, 9, 10)))
        s.delete(row)
        s.flush()
        g = detect_gaps(s, "FR0000120271", calendars.get("XPAR"), date(2026, 9, 1), DAY)
        assert g.missing == [date(2026, 9, 10)]
        # Xetra: 24/12/2026 absent is NOT a gap (venue closed) — checked on a synthetic window
        g2 = detect_gaps(s, "DE0007164600", calendars.get("XETR"), date(2026, 12, 21), date(2026, 12, 28))
        assert date(2026, 12, 24) not in g2.missing and date(2026, 12, 28) in g2.missing


def test_features_rs_rank_and_universe_snapshot(seeded, raw, calendars):
    # 3 instruments only: percentile ranks are 75 / 75 / 0, so the leader threshold is lowered for this fixture
    from app.config import validate_params

    raw["universe"]["momentum_watchlist_rs_rank_min"] = 70
    config = validate_params(raw)
    rows = [
        SymbolRow("FR0000120271", "TotalEnergies", "TTE", "PA", "EUR", "FR", market_cap_eur=1.3e11, sector="Energy"),
        SymbolRow("FR0000120073", "Air Liquide", "AI", "PA", "EUR", "FR", market_cap_eur=9e10, sector="Chemicals"),
        SymbolRow("DE0007164600", "SAP", "SAP", "XETRA", "EUR", "DE", market_cap_eur=2.5e11, sector="Tech"),
        SymbolRow("JE00B4T3BW64", "Glencore-like", "GLEN", "PA", "EUR", None, market_cap_eur=5e10),  # trap, no prices
        SymbolRow("FR0000000001", "Micro", "MIC", "PA", "EUR", "FR", market_cap_eur=5e7),
    ]
    with db_session() as s:
        n = compute_features_for_day(s, DAY, ["FR0000120271", "FR0000120073", "DE0007164600"])
        assert n == 3
        feats = latest_features(s)
        assert feats["FR0000120271"].rs_rank > feats["FR0000120073"].rs_rank
        assert feats["FR0000120271"].sessions == 300 and feats["FR0000120271"].mm200 is not None
        rep = refresh_universe(s, config, calendars, rows, DAY)
        assert rep.total == 5 and rep.included == 3 and rep.added == ["DE0007164600", "FR0000120073", "FR0000120271"]
        micro = s.get(Instrument, "FR0000000001")
        assert not micro.in_universe and any("capitalisation" in r for r in micro.universe_reasons)
        trap = s.get(Instrument, "JE00B4T3BW64")
        assert (
            trap.pea_eligible is False
            and "piège" in (s.scalar(select(Instrument.pea_source).where(Instrument.isin == trap.isin)) or "") is False
            or trap.pea_source == "isin_prefix"
        )
        tte = s.get(Instrument, "FR0000120271")
        assert tte.pea_eligible is True and tte.srd_status == "complet" and tte.adv_eur_20 > 1e6 and tte.mic == "XPAR"
        sap = s.get(Instrument, "DE0007164600")
        assert sap.srd_status == "none" and sap.mic == "XETR"
        wl = list(s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True))))
        assert {w.isin for w in wl} == {
            "FR0000120271",
            "DE0007164600",
        } and rep.watchlist == 2  # flat series is not a leader
        assert "EURONEXT:TTE" in tradingview_export(s) and "XETR:SAP" in tradingview_export(s)
        # second refresh: no diff, snapshot versioned
        rep2 = refresh_universe(s, config, calendars, rows[:3], DAY)
        assert (
            rep2.added == []
            and rep2.removed == []
            and s.scalar(select(UniverseSnapshot).where(UniverseSnapshot.id == rep2.snapshot_id)).count == 3
        )
        rep3 = refresh_universe(s, config, calendars, rows[:2], DAY)
        assert rep3.removed == ["DE0007164600"]


def test_regime_job_on_stored_data(seeded, config, calendars):
    with db_session() as s:
        for isin, ex, code in [
            ("FR0000120271", "PA", "TTE"),
            ("FR0000120073", "PA", "AI"),
            ("DE0007164600", "XETRA", "SAP"),
        ]:
            s.add(
                Instrument(isin=isin, name=isin, mic="XPAR", exchange=ex, ticker_eodhd=f"{code}.{ex}", in_universe=True)
            )
    ctx = JobContext(config, calendars, DAY, ("XPAR",), "normal")
    light = compute_and_store(ctx)
    with db_session() as s:
        row = s.scalar(select(MarketRegimeRow))
        assert row.regime == light in ("green", "orange", "red")
        assert row.cac_vs_mm50 is True and row.stoxx_vs_mm50 is not None
        from app.domain.indicators import sma
        from app.services.market_data import load_bars

        expected = 0
        for isin in ("FR0000120271", "FR0000120073", "DE0007164600"):
            closes = [b.close for b in load_bars(s, isin, end=DAY, limit=60)]
            expected += closes[-1] > sma(closes, 50)
        assert row.breadth_mm50 == pytest.approx(expected / 3)
        assert row.details["missing"] == []


def test_srd_liquidation_alert_j_minus_3(db_engine, config, calendars):
    from app.notify.base import Notifier
    from app.scheduler.jobs.portfolio_sync import srd_liquidation_alerts
    from app.services.alerts import AlertService
    from app.services.portfolio import declare_execution, ensure_accounts

    sent = []
    with db_session() as s:
        acc = ensure_accounts(s)["cto_srd"]
        declare_execution(
            s, acc.id, "FR0000120271", "buy", 100, 42.4, datetime(2026, 9, 10, 8, 0, tzinfo=UTC), "web", mode="srd"
        )
    ctx = JobContext(config, calendars, date(2026, 9, 22), ("XPAR",), "normal")  # liquidation 25/09 → 3 sessions left
    ctx.alerts = AlertService(Notifier(telegram_send=lambda e: sent.append(e)), 15)
    ctx.mode = "reunion"
    assert srd_liquidation_alerts(ctx) == 1 and "J−3" in sent[0].title
    ctx2 = JobContext(config, calendars, date(2026, 9, 15), ("XPAR",), "normal")
    ctx2.alerts, ctx2.mode = ctx.alerts, "reunion"
    assert srd_liquidation_alerts(ctx2) == 0
