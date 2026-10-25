"""A feed whose rows are refused at flush (Postgres length/NOT NULL) must not poison the session for
the next feeds (the production error was « transaction has been rolled back due to a previous
exception during flush » on every feed after the first)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.data.providers.rss import NewsItem as RssItem
from app.db.models import DataFreshness, NewsItem
from app.db.session import db_session
from app.scheduler.jobs import news_scan
from app.scheduler.runner import JobContext


def _item(source: str, guid: str) -> RssItem:
    now = datetime.now(UTC)
    return RssItem(
        source=source,
        guid=guid,
        title=f"Titre {guid[:20]}",
        url=f"https://example.org/{guid[:10]}",
        body="corps",
        published_at=now,
        fetched_at=now,
        dedup_hash=f"{source}-{guid}"[:64],
    )


def test_one_refused_feed_does_not_break_the_others(db_engine, config, calendars, monkeypatch):
    def fake_fetch(name: str, url: str) -> list[RssItem]:
        return [_item(name, "x" * 600)]  # a 600-char guid (long RSS URLs) must be accepted

    def fake_ingest(s, items, gateway, max_items=50, **kw):
        if items[0].source == "bad":
            s.add(NewsItem(source="bad", guid="g", dedup_hash="h", fetched_at=datetime.now(UTC), title=None))
            s.flush()  # NOT NULL refused by the database → IntegrityError inside the savepoint
        from app.services.news import ingest_rss_items

        return ingest_rss_items(s, items, gateway, max_items, **kw)

    monkeypatch.setattr(news_scan, "fetch_feed", fake_fetch)
    monkeypatch.setattr(news_scan, "ingest_rss_items", fake_ingest)
    monkeypatch.setattr(
        config.params.data, "rss_feeds", [{"name": "bad", "url": "https://b"}, {"name": "good", "url": "https://g"}]
    )
    ctx = JobContext(config, calendars, datetime.now(UTC).date(), ("XPAR",), "normal")
    assert news_scan.run(ctx) == 1  # the good feed was ingested after the bad one failed
    with db_session() as s:
        rows = list(s.scalars(select(NewsItem)))
        assert [r.source for r in rows] == ["good"] and len(rows[0].guid) == 600
        bad = s.get(DataFreshness, "rss:bad")
        assert bad is not None and bad.last_error and "IntegrityError" in bad.last_error
        assert s.get(DataFreshness, "rss:good").last_error is None


def test_news_guid_column_is_text():
    from sqlalchemy import Text

    assert isinstance(NewsItem.__table__.c.guid.type, Text)


def test_podcast_feeds_read_recent_episodes_only_and_every_two_hours(db_engine, config, calendars, monkeypatch):
    """docs/23 §23.7: a podcast feed carries its whole history (1 605 episodes for C'est votre argent):
    only the last ``media.recent_days`` are ingested, and the feed is read every ``media.poll_minutes``."""
    from datetime import timedelta

    calls: list[str] = []
    now = datetime.now(UTC)

    def fake_fetch(name: str, url: str) -> list[RssItem]:
        calls.append(name)
        old = _item(name, "old")
        old = RssItem(**{**old.__dict__, "published_at": now - timedelta(days=400), "dedup_hash": f"{name}-old"})
        return [_item(name, "new"), old]

    monkeypatch.setattr(news_scan, "fetch_feed", fake_fetch)
    monkeypatch.setattr(news_scan, "_media", lambda *a, **k: 0)
    monkeypatch.setattr(
        config.params.data,
        "rss_feeds",
        [{"name": "pod", "url": "https://p", "kind": "media_conseil"}, {"name": "news", "url": "https://n"}],
    )
    ctx = JobContext(config, calendars, now.date(), ("XPAR",), "normal")
    assert news_scan.run(ctx) == 3  # pod: the recent episode only; news: both items
    assert news_scan.run(ctx) == 0 and calls == ["pod", "news", "news"]  # pod not re-read within 2 h
