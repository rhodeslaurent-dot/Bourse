"""Live quotes for display (user request of 28/10/2026): Saxo every 15 s, EODHD every 15 min or on a manual
refresh (minimal interval, daily budget), delayed status kept, nothing fetched outside the venue's session,
Uic resolved through the reference endpoint, endpoint JSON and page hooks. Fakes only, no network."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import validate_params
from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote
from app.db.models import Instrument, PriceEod
from app.db.session import db_session
from app.services.live_quotes import Fetchers, LiveQuoteService, set_service

T = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)  # Wednesday 11:00 Paris: Euronext and Xetra in session
A, B, C = "FR0000120271", "FR0000131104", "NL0000235190"


def _q(symbol: str, last: float, at: datetime, source: str, status=DataStatus.REALTIME, delay: int = 0) -> Quote:
    return Quote(
        source=source,
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=at,
        processed_at=at,
        data_status=status,
        symbol=symbol,
        market_timestamp=at - timedelta(minutes=delay),
        price_type=PriceType.LAST,
        last=last,
        declared_delay_minutes=delay,
    )


class Fake:
    def __init__(self, uics: dict[str, str] | None = None, saxo_status=DataStatus.REALTIME) -> None:
        self.saxo_calls: list[dict[str, list[str]]] = []
        self.eodhd_calls: list[list[str]] = []
        self.lookups: list[str] = []
        self.uics = uics or {}
        self.now = T
        self.saxo_status = saxo_status
        self.saxo_price = 42.0
        self.saxo_fails = False

    def saxo(self, by_type: dict[str, list[str]]) -> list[Quote]:
        self.saxo_calls.append(by_type)
        if self.saxo_fails:
            raise RuntimeError("Saxo 503")
        delay = 15 if self.saxo_status == DataStatus.DELAYED else 0
        return [
            _q(u, self.saxo_price, self.now, "saxo", self.saxo_status, delay) for us in by_type.values() for u in us
        ]

    def resolve(self, isin: str, mic: str) -> str | None:
        self.lookups.append(isin)
        return self.uics.get(isin)

    def eodhd(self, isins: list[str]) -> dict[str, Quote]:
        self.eodhd_calls.append(list(isins))
        return {i: _q(i, 50.0, self.now, "eodhd_rest", DataStatus.DELAYED, 15) for i in isins}


@pytest.fixture
def fake(db_engine):
    f = Fake()
    with db_session() as s:
        s.add(Instrument(isin=A, name="TotalEnergies", mic="XPAR", saxo_uic="111"))
        s.add(Instrument(isin=B, name="Sodexo", mic="XPAR"))
        s.add(Instrument(isin=C, name="Airbus", mic="XPAR"))
        for isin, close in ((A, 40.0), (B, 48.0), (C, 150.0)):
            for d, c in ((date(2026, 10, 26), close * 0.99), (date(2026, 10, 27), close)):
                s.add(PriceEod(isin=isin, day=d, open=c, high=c, low=c, close=c, volume=1, source="eodhd",
                               received_at=T, data_status="eod"))  # fmt: skip
    yield f
    set_service(None)


def _svc(f: Fake) -> LiveQuoteService:
    return LiveQuoteService(Fetchers(f.saxo, f.resolve, f.eodhd))


def _cfg(raw, **lq):
    raw = dict(raw)
    raw["live_quotes"] = {**raw.get("live_quotes", {}), **lq}
    return validate_params(raw, path=Path("config/params.example.yaml"), sha256="test")


def _refresh(svc, config, calendars, isins, at, force=False):
    with db_session() as s:
        return svc.refresh(s, config, calendars, isins, force=force, now=at)


def test_saxo_every_15_s_and_eodhd_every_15_min(fake, config, calendars):
    svc = _svc(fake)
    res = _refresh(svc, config, calendars, [A, B], T)
    assert fake.saxo_calls == [{"Stock": ["111"]}] and fake.eodhd_calls == [[B]]
    assert res.views[A].source == "saxo" and res.views[A].status == "realtime"
    assert res.next_poll_s == 15
    fake.now = T + timedelta(seconds=5)
    _refresh(svc, config, calendars, [A, B], fake.now)
    assert len(fake.saxo_calls) == 1 and len(fake.eodhd_calls) == 1  # both cached
    fake.now = T + timedelta(seconds=15)
    _refresh(svc, config, calendars, [A, B], fake.now)
    assert len(fake.saxo_calls) == 2 and len(fake.eodhd_calls) == 1  # Saxo again, EODHD not yet
    fake.now = T + timedelta(minutes=15)
    _refresh(svc, config, calendars, [A, B], fake.now)
    assert len(fake.eodhd_calls) == 2


def test_eodhd_only_polls_at_its_own_cadence(fake, config, calendars):
    res = _refresh(_svc(fake), config, calendars, [B], T)
    assert res.next_poll_s == 15 * 60 and not res.saxo_served


def test_delayed_status_is_kept_however_recent_the_download(fake, config, calendars):
    v = _refresh(_svc(fake), config, calendars, [B], T).views[B]
    assert v.status == "delayed" and v.label == "différé 15 min" and v.received_at == T
    assert v.as_json()["ton"] == "amber"
    assert v.variation == pytest.approx(50.0 / 48.0 - 1)  # vs the last stored close


def test_manual_refresh_minimal_interval(fake, config, calendars):
    svc = _svc(fake)
    _refresh(svc, config, calendars, [B], T)
    fake.now = T + timedelta(minutes=2)
    res = _refresh(svc, config, calendars, [B], fake.now, force=True)
    assert res.manual_accepted and len(fake.eodhd_calls) == 2 and "budget EODHD" in res.message
    fake.now = T + timedelta(minutes=2, seconds=30)
    res = _refresh(svc, config, calendars, [B], fake.now, force=True)
    assert not res.manual_accepted and "refusé" in res.message and len(fake.eodhd_calls) == 2


def test_daily_budget_exhausted_keeps_the_last_values(fake, raw, calendars):
    config = _cfg(raw, eodhd_daily_calls_max=2)
    svc = _svc(fake)
    res = _refresh(svc, config, calendars, [B, C], T)
    assert res.eodhd_left == 0 and not res.budget_exhausted
    fake.now = T + timedelta(minutes=15)
    res = _refresh(svc, config, calendars, [B, C], fake.now)
    assert res.budget_exhausted and "épuisé" in res.message and len(fake.eodhd_calls) == 1
    assert res.views[B].last == 50.0  # last value kept, its status honest (market age grows)
    next_day = T + timedelta(days=1)  # new day: new budget
    fake.now = next_day
    assert _refresh(svc, config, calendars, [B], next_day).eodhd_used == 1


def test_closed_venue_fetches_nothing_and_shows_the_last_close(fake, config, calendars):
    svc = _svc(fake)
    evening = datetime(2026, 10, 28, 19, 0, tzinfo=UTC)  # 20:00 Paris
    res = _refresh(svc, config, calendars, [A, B], evening)
    assert fake.saxo_calls == [] and fake.eodhd_calls == []
    assert res.views[A].status == "eod" and res.views[A].label == "clôture du 27/10" and res.views[A].last == 40.0
    assert res.next_poll_s is None


def test_unknown_venue_is_never_fetched(fake, config, calendars):
    with db_session() as s:
        s.add(Instrument(isin="XS0000000001", name="Inconnue", mic="ZZZZ"))
    res = _refresh(_svc(fake), config, calendars, ["XS0000000001"], T)
    assert fake.eodhd_calls == [] and res.views["XS0000000001"].status == "unavailable"


def test_uic_resolved_lazily_and_stored_never_guessed(fake, config, calendars):
    fake.uics = {B: "222"}
    svc = _svc(fake)
    res = _refresh(svc, config, calendars, [B, C], T)
    assert res.views[B].source == "saxo" and res.views[C].source == "eodhd_rest"
    with db_session() as s:
        assert s.get(Instrument, B).saxo_uic == "222" and s.get(Instrument, C).saxo_uic is None
    fake.now = T + timedelta(seconds=20)
    _refresh(svc, config, calendars, [B, C], fake.now)
    assert fake.lookups == [B, C]  # C not looked up again the same day


def test_saxo_without_subscription_stays_delayed(fake, config, calendars):
    fake.saxo_status = DataStatus.DELAYED
    v = _refresh(_svc(fake), config, calendars, [A], T).views[A]
    assert v.source == "saxo" and v.status == "delayed"


def test_disabled_fetches_nothing(fake, raw, calendars):
    res = _refresh(_svc(fake), _cfg(raw, enabled=False), calendars, [A, B], T)
    assert fake.saxo_calls == [] and fake.eodhd_calls == [] and res.next_poll_s is None
    assert res.views[B].status == "unavailable"


# --- endpoint and pages ---------------------------------------------------------------------------------------


@pytest.fixture
def client(config, calendars, fake, monkeypatch):
    from app.main import create_app

    set_service(_svc(fake))
    monkeypatch.setattr("app.api.live_quotes_routes._now", lambda: fake.now)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def _proposal(s, isin: str, created: datetime):
    from app.services.decisions import create_proposal, record_signal

    sig = record_signal(s, isin, "d7_trend_quality", "moteur", created, "eod", "eod", "t", stop_initial=36.0)
    return create_proposal(
        s, sig, "WATCH", {isin: {"last": 41.0, "data_status": "eod"}},
        {"blocked": ["WATCH : refusé — P_max 40 dépassé — limite posée en attente d'un repli"]}, "t",
        created_at=created, stop=36.0, targets=[48.0], account_recommended="pea",
        details={"admission": "refuse", "p_max": 40.0, "comptes": {"pea": {"frais_aller_retour_pct": 0.0}}},
    )  # fmt: skip


def test_endpoint_json(client, fake):
    with db_session() as s:
        pid = _proposal(s, A, T - timedelta(hours=2)).id
    r = client.get(f"/api/cours?isins={A},{B},bad&force=0")
    assert r.status_code == 200
    data = r.json()
    assert set(data["cours"]) == {A, B}
    qa = data["cours"][A]
    assert qa["cours"] == 42.0 and qa["statut"] == "realtime" and qa["statut_txt"] == "temps réel"
    assert qa["source"] == "saxo" and qa["heure"] == "14:00" and "Réunion" in qa["heure_txt"]
    assert data["cours"][B]["statut"] == "delayed"
    assert data["ecarts"][str(pid)]["ecart"] == pytest.approx(42.0 / 40.0 - 1)
    assert data["next_poll_s"] == 15 and data["eodhd"]["utilises"] == 1
    fake.now = T + timedelta(seconds=10)
    assert client.get(f"/api/cours?isins={B}&force=1").json()["manuel"]["accepte"]
    refused = client.get(f"/api/cours?isins={B}&force=1").json()
    assert refused["manuel"] == {"demande": True, "accepte": False} and "refusé" in refused["message"]


def test_pages_carry_the_live_hooks(client, fake):
    from app.db.models import WatchlistRow

    with db_session() as s:
        s.add(WatchlistRow(isin=A, reason="manual", source="manual", active=True, added_at=T))
    html = client.get("/watchlist").text
    assert f'data-live-isin="{A}"' in html and "Rafraîchir les cours" in html and "/static/live_quotes.js" in html


def test_opportunities_card_and_order_book_update_live(client):
    from app.db.models import ProposalReevaluation

    now = datetime.now(UTC)
    with db_session() as s:
        pid = _proposal(s, A, now).id
        s.add(ProposalReevaluation(proposal_id=pid, isin=A, ts=now, price=39.5, source="saxo", market_timestamp=now,
                                   data_status="delayed", status="entree_atteinte", actionable=False,
                                   reasons=["à vérifier : cours différé 15 min"]))  # fmt: skip
    html = client.get("/opportunites").text
    assert f'data-live-distance="{pid}"' in html and f'data-live-isin="{A}"' in html
    assert "prix d&#39;entrée atteint — cours différé 15 min, à vérifier" in html
    assert "Sorties non validées par la recherche" in html  # user decision of 29/10/2026, next to the targets


def test_an_implausible_quote_never_reaches_the_reevaluation(fake, config, calendars):
    fake.saxo_price = 30.0  # −25 % against the last close (40), no news
    svc = _svc(fake)
    with db_session() as s:
        assert svc.refresh(s, config, calendars, [A], now=T).views[A].plausible is False
        assert svc.quotes(s, config, calendars, [A], now=T + timedelta(seconds=15)) == {}


def test_a_cached_quote_is_given_with_its_status_at_read_time(fake, config, calendars):
    fake.saxo_price = 41.0  # within the plausibility gap of the last close (40)
    svc = LiveQuoteService(Fetchers(fake.saxo, fake.resolve, None))  # no EODHD to take over
    with db_session() as s:
        assert svc.quotes(s, config, calendars, [A], now=T)[A].data_status == DataStatus.REALTIME
        fake.saxo_fails = True
        later = T + timedelta(minutes=40)
        fake.now = later
        q = svc.quotes(s, config, calendars, [A], now=later)[A]
    assert q.data_status == DataStatus.STALE and q.market_timestamp == T  # downloaded at T, never « temps réel »


def test_a_slow_fetch_never_blocks_the_other_readers(fake, config, calendars):
    svc = _svc(fake)
    _refresh(svc, config, calendars, [A], T)
    assert svc._fetch_lock.acquire()  # another round is fetching (a source hanging on its timeout)
    try:
        fake.now = T + timedelta(seconds=20)
        res = _refresh(svc, config, calendars, [A], fake.now)
    finally:
        svc._fetch_lock.release()
    assert len(fake.saxo_calls) == 1 and res.views[A].last == 42.0  # answered from the cache, no wait
