"""docs/26 S3 — retail attention from forums and social networks (decision of the user, 25/10/2026: forums
included, risks accepted). Only **timestamps and counts** leave this module: no message text, no author,
no pseudonym is returned or stored (third parties' personal data). Message texts are read in memory only to
find which value they name, then dropped.

- Boursorama forum: the first page of a value's forum, dates of the messages (format to verify on the PC
  with ``social-check``; the parser accepts « 25/10/2026 14:32 », « 25 oct. 2026 », « aujourd'hui », « hier »).
- Reddit: official API (OAuth « application only »), new posts and comments of chosen subreddits.
- Bluesky: official API (app password), recent posts for a cashtag or a name.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from app.data.base import ProviderError

UA = "BoursePilot/1.0 (outil personnel; https://github.com/rhodeslaurent-dot/Bourse)"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

_MONTHS = {
    "janv": 1, "jan": 1, "févr": 2, "fevr": 2, "fév": 2, "fev": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "août": 8, "aout": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
}  # fmt: skip
_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_TXT = re.compile(
    r"\b(\d{1,2})\s+(janv|jan|févr|fevr|fév|fev|mars|avr|mai|juin|juil|août|aout|sept|oct|nov|déc|dec)\.?\s+(\d{4})\b",
    re.I,
)  # noqa: E501
_REL = re.compile(r"\b(aujourd'hui|aujourd’hui|hier|il y a \d+\s*(?:min|h|heure))", re.I)


@dataclass(frozen=True)
class Count:
    day: date
    messages: int
    truncated: bool  # the page held only this day's messages: the count is a floor


_TIME = re.compile(r"^\s*(?:à\s*)?(\d{1,2})[:h](\d{2})")
_AGO = re.compile(r"il y a (\d+)\s*(min|h|heure)", re.I)


def forum_times(html: str, now: datetime) -> list[tuple[date, datetime | None]]:
    """(date, time when shown) of each message of a forum page, in reading order; ``now`` in the forum's
    time zone (Paris). Unknown formats give nothing (never a guess)."""
    out: list[tuple[date, datetime | None]] = []
    text = re.sub(r"<[^>]+>", " ", html)
    today = now.date()
    for m in re.finditer(f"{_NUM.pattern}|{_TXT.pattern}|{_REL.pattern}", text, re.I):
        g = m.group(0).lower()
        tail = text[m.end() : m.end() + 12]
        tm = _TIME.match(tail)
        try:
            if m.group(1):
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            elif m.group(4):
                d = date(int(m.group(6)), _MONTHS[m.group(5).lower()], int(m.group(4)))
            elif g.startswith("hier"):
                d = today - timedelta(days=1)
            elif g.startswith("il y a"):
                a = _AGO.match(g)
                if a is None:
                    continue
                delta = timedelta(minutes=int(a.group(1))) if a.group(2) == "min" else timedelta(hours=int(a.group(1)))
                ts = now - delta
                out.append((ts.date(), ts))
                continue
            else:
                d = today
        except (ValueError, KeyError):
            continue
        if not (today - timedelta(days=400) <= d <= today):
            continue
        ts = None
        if tm is not None and int(tm.group(1)) < 24 and int(tm.group(2)) < 60:
            ts = datetime(d.year, d.month, d.day, int(tm.group(1)), int(tm.group(2)), tzinfo=now.tzinfo)
        out.append((d, ts))
    return out


def forum_dates(html: str, today: date) -> list[date]:
    """Dates of the messages of a forum page, in reading order."""
    now = datetime.combine(today, datetime.max.time().replace(microsecond=0))
    return [d for d, _ in forum_times(html, now)]


def daily_counts(dates: list[date]) -> list[Count]:
    """Messages per day from the dates of one page; the oldest day shown may be cut by the page end."""
    if not dates:
        return []
    by: dict[date, int] = {}
    for d in dates:
        by[d] = by.get(d, 0) + 1
    oldest = min(by)
    return [Count(d, n, d == oldest and len(by) == 1) for d, n in sorted(by.items())]


def boursorama_page(url: str, client: httpx.Client, timeout: float = 20) -> str:
    r = client.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "fr-FR,fr;q=0.9"}, timeout=timeout)
    if r.status_code != 200:
        raise ProviderError(f"forum : HTTP {r.status_code}")
    return r.text


def reddit_token(client_id: str, secret: str, client: httpx.Client) -> str:
    r = client.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(client_id, secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": UA},
        timeout=20,
    )
    if r.status_code != 200 or "access_token" not in r.json():
        raise ProviderError(f"Reddit : jeton refusé (HTTP {r.status_code})")
    return str(r.json()["access_token"])


def reddit_listing(
    sub: str, kind: str, token: str, client: httpx.Client, limit: int = 100
) -> list[tuple[datetime, str]]:
    """(created_at, text) of the newest posts (kind ``new``) or comments (``comments``) of a subreddit."""
    r = client.get(
        f"https://oauth.reddit.com/r/{sub}/{kind}",
        params={"limit": limit},
        headers={"User-Agent": UA, "Authorization": f"bearer {token}"},
        timeout=20,
    )
    if r.status_code != 200:
        raise ProviderError(f"Reddit r/{sub} : HTTP {r.status_code}")
    out = []
    for ch in (r.json().get("data") or {}).get("children") or []:
        d = ch.get("data") or {}
        ts = d.get("created_utc")
        if ts is None:
            continue
        text = " ".join(str(d.get(k) or "") for k in ("title", "selftext", "body"))
        out.append((datetime.fromtimestamp(float(ts), UTC), text))
    return out


def bluesky_session(handle: str, app_password: str, client: httpx.Client) -> str:
    r = client.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=20,
    )
    if r.status_code != 200:
        raise ProviderError(f"Bluesky : session refusée (HTTP {r.status_code})")
    return str(r.json()["accessJwt"])


def bluesky_count(query: str, since: datetime, token: str, client: httpx.Client, pause_s: float = 0.5) -> int:
    """Number of posts matching ``query`` since ``since`` (at most 100: a floor beyond)."""
    r = client.get(
        "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
        params={"q": query, "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 100, "sort": "latest"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    time.sleep(pause_s)
    if r.status_code != 200:
        raise ProviderError(f"Bluesky : HTTP {r.status_code}")
    return len(r.json().get("posts") or [])


def reddit_search(sub: str, query: str, token: str, client: httpx.Client, limit: int = 100) -> list[datetime]:
    """Timestamps of the posts of a subreddit matching ``query`` over the last year (for the baseline)."""
    r = client.get(
        f"https://oauth.reddit.com/r/{sub}/search",
        params={"q": query, "restrict_sr": 1, "sort": "new", "t": "year", "limit": limit},
        headers={"User-Agent": UA, "Authorization": f"bearer {token}"},
        timeout=20,
    )
    if r.status_code != 200:
        raise ProviderError(f"Reddit r/{sub} recherche : HTTP {r.status_code}")
    out = []
    for ch in (r.json().get("data") or {}).get("children") or []:
        ts = (ch.get("data") or {}).get("created_utc")
        if ts is not None:
            out.append(datetime.fromtimestamp(float(ts), UTC))
    return out
