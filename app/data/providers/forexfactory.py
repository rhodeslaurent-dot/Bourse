"""Economic calendar from the public ForexFactory weekly feed (free JSON, no key): central banks,
inflation, employment… Time zone given in the feed; stored in UTC. Read-only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.data.base import ProviderError

FEEDS = {
    "thisweek": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "nextweek": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}


@dataclass(frozen=True)
class CalendarEvent:
    ts: datetime  # UTC
    currency: str
    title: str
    impact: str  # High | Medium | Low | Holiday
    forecast: str | None
    previous: str | None


def parse(items: list[dict]) -> list[CalendarEvent]:
    out: list[CalendarEvent] = []
    for it in items:
        raw = it.get("date")
        title = str(it.get("title") or "").strip()
        if not raw or not title:
            continue
        try:
            ts = datetime.fromisoformat(str(raw)).astimezone(UTC)
        except ValueError:
            continue
        out.append(
            CalendarEvent(
                ts,
                str(it.get("country") or "").upper(),
                title[:160],
                str(it.get("impact") or ""),
                (str(it.get("forecast")) or None) if it.get("forecast") not in (None, "") else None,
                (str(it.get("previous")) or None) if it.get("previous") not in (None, "") else None,
            )
        )
    return out


def fetch(feed: str = "thisweek", client: httpx.Client | None = None) -> list[CalendarEvent]:
    c = client or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    try:
        r = c.get(FEEDS[feed])
        if r.status_code == 404 and feed == "nextweek":
            return []  # published later in the week
        if r.status_code >= 400:
            raise ProviderError(f"calendrier ForexFactory {r.status_code}")
        data = r.json()
        return parse(data if isinstance(data, list) else [])
    except httpx.HTTPError as exc:
        raise ProviderError(f"calendrier ForexFactory indisponible : {exc.__class__.__name__}") from exc
    finally:
        if client is None:
            c.close()
