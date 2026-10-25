"""docs/26 S3 — forum counts and Reddit mentions stored as daily counts (no text, no author), true zeros
when the page covers the day, and a radar label from the social baseline."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from app.db.models import AttentionDaily, FeatureDaily, Instrument, PriceEod, WeakSignalHit
from app.db.session import db_session
from app.services.social import collect_forum, collect_reddit
from app.services.weak_signals import compute_hits

DAY = date(2026, 10, 23)


def _inst(s, isin="FR0000000005", name="Exemplaire", ticker="EXM"):
    s.add(Instrument(isin=isin, name=name, ticker_local=ticker, mic="XPAR", currency="EUR", in_universe=True,
                     active=True))  # fmt: skip
    s.add(FeatureDaily(isin=isin, day=DAY, rs_rank=99, atr14=0.3, sessions=61))
    s.flush()


def test_forum_counts_and_true_zero(db_engine, config, monkeypatch):
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["boursorama"], "interval_s", 0)
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["boursorama"], "date_class", None)  # generic page
    pages = {"EXM": "<p>23/10/2026 10:00</p>" * 7 + "<p>21/10/2026 09:00</p>"}

    def handler(req: httpx.Request) -> httpx.Response:
        assert "1rPEXM" in str(req.url)
        return httpx.Response(200, text=pages["EXM"])

    with db_session() as s, httpx.Client(transport=httpx.MockTransport(handler)) as c:
        _inst(s)
        n, errors = collect_forum(s, config, c, DAY)
        rows = {r.day: r.value for r in s.query(AttentionDaily).filter(AttentionDaily.source == "forum_boursorama")}
        assert not errors and rows == {DAY: 7, DAY - timedelta(days=1): 0}  # 22/10 covered by the page: 0


def test_reddit_mentions_counted_without_text(db_engine, config, monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["reddit"], "interval_s", 0)
    monkeypatch.setitem(config.raw["weak_signals"]["social"]["reddit"], "subreddits", ["bourse"])
    ts = datetime(2026, 10, 23, 12, 0, tzinfo=UTC).timestamp()

    def handler(req: httpx.Request) -> httpx.Response:
        if "access_token" in str(req.url):
            return httpx.Response(200, json={"access_token": "t"})
        old = datetime(2026, 10, 20, tzinfo=UTC).timestamp()
        kids = [{"data": {"created_utc": ts, "title": "Exemplaire va exploser", "author": "x"}},
                {"data": {"created_utc": ts, "body": "rien à voir"}},
                {"data": {"created_utc": old, "body": "vieux message"}}]  # fmt: skip
        return httpx.Response(200, json={"data": {"children": kids}})

    with db_session() as s, httpx.Client(transport=httpx.MockTransport(handler)) as c:
        _inst(s)
        n, errors = collect_reddit(s, config, c, DAY)
        rows = {(r.isin, r.day): r.value for r in s.query(AttentionDaily).filter(AttentionDaily.source == "reddit")}
        assert rows[("FR0000000005", DAY)] == 2  # one post per listing (new + comments), the author never kept
        assert rows[("FR0000000005", DAY - timedelta(days=1))] == 0  # yesterday fully read: a true zero


def test_social_spike_gives_an_early_buzz_idea(db_engine, config):
    with db_session() as s:
        _inst(s)
        for k in range(61):
            d = DAY - timedelta(days=60 - k)
            s.add(PriceEod(isin="FR0000000005", day=d, open=10, high=10.1, low=9.9, close=10.0, volume=100_000,
                           source="eodhd", received_at=datetime.now(UTC), data_status="eod"))  # fmt: skip
            s.add(AttentionDaily(isin="FR0000000005", day=d, source="forum_boursorama", lang="-",
                                 value=40 if k >= 58 else 3 + k % 2, received_at=datetime.now(UTC)))  # fmt: skip
        s.flush()
        hits = compute_hits(s, config, DAY, now=datetime(2026, 10, 24, 5, 40, tzinfo=UTC))
        assert [(h.label, h.idea) for h in hits] == [("buzz_precoce", True)]
        assert s.query(WeakSignalHit).one().details["z_forums_reseaux"]["forum_boursorama"] > 2.5
