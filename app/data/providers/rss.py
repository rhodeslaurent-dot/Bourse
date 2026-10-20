"""RSS/Atom ingestion (docs/04 §4.5): browser User-Agent, dedup by GUID/URL, publication time
kept, full text stored (personal use). Parsing is pure; fetching is a thin wrapper."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import feedparser
import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


@dataclass(frozen=True)
class NewsItem:
    source: str
    guid: str
    title: str
    url: str
    published_at: datetime | None
    fetched_at: datetime
    body: str
    dedup_hash: str


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()


def _published(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
                except ValueError:
                    continue
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime(*st[:6], tzinfo=UTC)
    return None


def parse_feed(source: str, content: str | bytes, fetched_at: datetime | None = None) -> list[NewsItem]:
    """Parse feed XML → items. Raises ``ValueError`` when the content is not a feed (HTML error
    page, malformed XML); an empty but valid feed returns [] and the caller counts the days."""
    fetched_at = fetched_at or datetime.now(UTC)
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.get("bozo") or not parsed.feed.get("title") or not parsed.get("version")):
        raise ValueError(f"flux {source} illisible : {parsed.get('bozo_exception') or 'pas un flux RSS/Atom'}")
    items: list[NewsItem] = []
    for e in parsed.entries:
        url = str(e.get("link") or "")
        guid = str(e.get("id") or url or e.get("title") or "")
        if not guid:
            continue
        body_html = ""
        if e.get("content"):
            body_html = " ".join(c.get("value", "") for c in e["content"])
        body = strip_html(body_html or e.get("summary", "") or e.get("description", ""))
        title = strip_html(str(e.get("title") or ""))
        items.append(
            NewsItem(
                source,
                guid,
                title,
                url,
                _published(e),
                fetched_at,
                body,
                hashlib.sha256(f"{source}|{guid}".encode()).hexdigest(),
            )
        )
    return items


def fetch_feed(
    source: str, url: str, client: httpx.Client | None = None, user_agent: str = BROWSER_UA
) -> list[NewsItem]:
    c = client or httpx.Client(timeout=30, follow_redirects=True)
    r = c.get(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
    )
    if r.status_code >= 400:
        raise ValueError(f"flux {source}: HTTP {r.status_code}")
    return parse_feed(source, r.content)
