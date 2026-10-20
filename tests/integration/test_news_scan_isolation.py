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

    def fake_ingest(s, items, gateway, max_items=50):
        if items[0].source == "bad":
            s.add(NewsItem(source="bad", guid="g", dedup_hash="h", fetched_at=datetime.now(UTC), title=None))
            s.flush()  # NOT NULL refused by the database → IntegrityError inside the savepoint
        from app.services.news import ingest_rss_items

        return ingest_rss_items(s, items, gateway, max_items)

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
