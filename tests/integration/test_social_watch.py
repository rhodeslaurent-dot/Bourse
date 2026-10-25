"""docs/26 S3 — the intraday watch: a burst of forum messages without publication and without a price move
gives a « buzz précoce » at once (no 30-day history needed), an alert and a sized D8 opportunity card."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from app.db.models import AttentionDaily, Proposal, Signal, WeakSignalHit
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.scheduler.jobs import social_watch
from app.scheduler.runner import JobContext
from app.services.social import backfill, watch
from tests.integration.test_engine import ISIN, _seed, breakout_bars

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)  # 16:00 Paris


def _page(now_paris: datetime, recent: int, older_hours: list[int]) -> str:
    rows = [f"<p>{(now_paris - timedelta(minutes=10 * k)):%d/%m/%Y %H:%M}</p>" for k in range(recent)]
    rows += [f"<p>{(now_paris - timedelta(hours=h)):%d/%m/%Y %H:%M}</p>" for h in older_hours]
    return "".join(rows)


def _client(html: str) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=html)))


def _setup(s, config, monkeypatch):
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["boursorama"], "interval_s", 0)
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["reddit"], "enabled", False)
    _seed(s, breakout_bars())
    from app.db.models import Instrument

    inst = s.get(Instrument, ISIN)
    inst.ticker_local, inst.active = "TTE", True
    s.flush()


def test_a_burst_without_news_is_caught_at_once(db_engine, config, monkeypatch):
    with db_session() as s:
        _setup(s, config, monkeypatch)
        html = _page(NOW.astimezone(PARIS), 7, [20, 30])  # 7 messages in 1 h, 1 in the 24 h before, page back 30 h
        hits = watch(s, config, _client(html), now=NOW)
        assert [h.label for h in hits] == ["buzz_precoce"] and hits[0].details["veille_intraday"]
        assert watch(s, config, _client(html), now=NOW + timedelta(minutes=20)) == []  # one label per day


def test_a_busy_forum_page_is_not_an_acceleration(db_engine, config, monkeypatch):
    with db_session() as s:
        _setup(s, config, monkeypatch)
        html = _page(NOW.astimezone(PARIS), 30, [])  # 30 messages in 5 h, the page reaches back no further
        assert watch(s, config, _client(html), now=NOW) == []


def test_the_job_alerts_and_prepares_a_d8_card(db_engine, config, calendars, monkeypatch):
    sent = []

    class Alerts:
        def emit(self, s, event, mode, *a, **k):
            sent.append(event)
            return True

    html = _page(NOW.astimezone(PARIS), 7, [20, 30])
    real_client = _client(html)

    class _Ctx:
        def __enter__(self):
            return real_client

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(social_watch.httpx, "Client", lambda **k: _Ctx())
    monkeypatch.setattr(social_watch, "watch", lambda s, cfg, c: watch(s, cfg, c, now=NOW))
    with db_session() as s:
        _setup(s, config, monkeypatch)
    ctx = JobContext(config, calendars, date(2026, 10, 22), ("XPAR",), "normal", alerts=Alerts(), mode="disponible")
    assert social_watch.run(ctx) == 1
    assert sent and sent[0].kind == "radar_buzz_precoce" and len(sent[0].lines) <= 11
    with db_session() as s:
        d8 = [p for p in s.query(Proposal) if (p.details or {}).get("niveau_risque") is not None]
        assert any(s.get(Signal, p.signal_id).detector == "d8_buzz_precoce" for p in d8)
        assert s.query(WeakSignalHit).one().label == "buzz_precoce"
        from app.db.models import SocialContent

        sc = s.query(SocialContent).one()  # docs/30: the content is read too (classes not set → nothing guessed)
        assert (sc.reason, sc.status) == ("radar", "structure")
    assert any(line.startswith("Forum : échanges du forum : indisponibles") for line in sent[0].lines)


def test_backfill_gives_a_baseline_from_day_one(db_engine, config, monkeypatch):
    day = date(2026, 10, 22)
    html = "".join(f"<p>{(day - timedelta(days=k)):%d/%m/%Y} 10:00</p>" for k in range(0, 70, 7))
    with db_session() as s:
        _setup(s, config, monkeypatch)
        notes = backfill(s, config, _client(html), day, [ISIN], days=60)
        rows = s.query(AttentionDaily).filter(AttentionDaily.source == "forum_boursorama").all()
        assert "forum : 1 valeur(s) rattrapée(s)" in notes and len(rows) == 61
        assert sum(r.value for r in rows) == 9 and min(r.day for r in rows) == day - timedelta(days=60)
