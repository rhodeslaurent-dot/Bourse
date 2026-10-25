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
from html.parser import HTMLParser

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
    r"\b(\d{1,2})\s+(janv|jan|févr|fevr|fév|fev|mars|avr|mai|juin|juil|août|aout|sept|oct|nov|déc|dec)[^\W\d_]*\.?\s+(\d{4})\b",
    re.I,
)  # noqa: E501
_REL = re.compile(r"\b(aujourd'hui|aujourd’hui|hier|il y a \d+\s*(?:min|h|heure))", re.I)
# « 24 oct. 14:32 » (Boursorama, last message of a thread): no year — the latest such date not in the future
_TXT_NOYEAR = re.compile(
    r"\b(\d{1,2})\s+(janv|jan|févr|fevr|fév|fev|mars|avr|mai|juin|juil|août|aout|sept|oct|nov|déc|dec)[^\W\d_]*\.?\s+"
    r"(\d{1,2})[:h](\d{2})\b",
    re.I,
)
_LEADING_TIME = re.compile(r"^\s*(\d{1,2})[:h](\d{2})\b")


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


_DATE_ANY = re.compile(f"{_NUM.pattern}|{_TXT.pattern}|{_REL.pattern}", re.I)
_OPEN_TAG = re.compile(r"<(\w+)\b[^>]*?\bclass=[\"']([^\"']+)[\"']", re.I)


def date_holders(html: str) -> dict[str, tuple[int, int]]:
    """Diagnostics (``social-check --structure``): for each « tag.class » holding a date on the page, (dates,
    dates with a time). Only markup names leave this function — never a text, an author or a pseudonym."""
    out: dict[str, tuple[int, int]] = {}
    for m in _DATE_ANY.finditer(html):
        before = list(_OPEN_TAG.finditer(html, max(0, m.start() - 400), m.start()))
        key = f"{before[-1].group(1).lower()}.{before[-1].group(2).strip()}" if before else "(sans classe)"
        timed = _TIME.match(re.sub(r"<[^>]+>", " ", html[m.end() : m.end() + 40])) is not None
        n, k = out.get(key, (0, 0))
        out[key] = (n + 1, k + int(timed))
    return dict(sorted(out.items(), key=lambda kv: -kv[1][0]))


def _no_year(text: str, now: datetime) -> datetime | None:
    """« 24 oct. 14:32 » → the latest such moment not after ``now`` (the format shows no year)."""
    m = _TXT_NOYEAR.search(text)
    if m is None:
        return None
    try:
        ts = datetime(now.year, _MONTHS[m.group(2).lower()], int(m.group(1)), int(m.group(3)), int(m.group(4)),
                      tzinfo=now.tzinfo)  # fmt: skip
    except (ValueError, KeyError):
        return None
    return ts.replace(year=now.year - 1) if ts.date() > now.date() else ts


def message_time(text: str, now: datetime) -> tuple[date | None, datetime | None]:
    """Date and time of one date field: « 24 oct. 14:32 », « 14:32 » alone (today), or the generic formats."""
    short = _no_year(text, now)
    if short is not None:
        return short.date(), short
    found = forum_times(text, now)
    if found:
        return found[0]
    bare = _LEADING_TIME.match(text)  # « 14:32 » (today) — possibly shown twice in the field
    if bare and int(bare.group(1)) < 24 and int(bare.group(2)) < 60:
        ts = now.replace(hour=int(bare.group(1)), minute=int(bare.group(2)), second=0, microsecond=0)
        return ts.date(), ts
    return None, None


def forum_times_in(html: str, now: datetime, date_class: str) -> list[tuple[date, datetime | None]]:
    """Date (and time) of each element whose class contains ``date_class``, read from that element's own
    text only — e.g. the « last message » column of a thread list: « 14:32 » (today), « 24 oct. 14:32 »."""
    c = _Collector({"message": date_class})
    c.feed(html)
    c.close()
    out: list[tuple[date, datetime | None]] = []
    for it in c.items:
        d, ts = message_time(it["all"], now)
        if d is not None and not (ts is not None and out and out[-1][1] == ts):  # same row shown twice
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


# --- docs/30 S4a: message contents of a forum thread, anonymised before they leave this module -------------

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_URL = re.compile(r"https?://(?:www\.)?([^/\s\"'<>]+)[^\s\"'<>]*", re.I)
_MENTION = re.compile(r"@\w[\w.\-]*")


@dataclass(frozen=True)
class ForumMessage:
    """One message: an ephemeral author key (salted hash — the pseudonym never leaves this module), the time
    when shown, and the text with every pseudonym of the page and every mention replaced by « [membre] »
    and each link reduced to its domain."""

    author_key: str
    day: date | None
    ts: datetime | None
    text: str


class _Collector(HTMLParser):
    """Text of the elements holding a message, its author, text and date — found by class (``selectors``:
    role → one class name, matched exactly, from ``weak_signals.social.boursorama``); the text of a ``quote``
    (another member's message quoted inside this one) is skipped; tolerant of unclosed tags."""

    def __init__(self, selectors: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.sel = {k: v for k, v in selectors.items() if v}
        self.stack: list[tuple[str, frozenset[str]]] = []
        self.cur: dict[str, list[str]] | None = None
        self.items: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        cls = next((v or "" for k, v in attrs if k == "class"), "")
        names = cls.split()
        roles = frozenset(r for r, token in self.sel.items() if token in names)
        if "message" in roles and self.cur is None:
            self.cur = {"all": [], "author": [], "text": [], "date": []}
        self.stack.append((tag, roles))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                popped = self.stack[i:]
                del self.stack[i:]
                break
        else:
            return
        if self.cur is not None and any("message" in r for _, r in popped):
            if not any("message" in r for _, r in self.stack):
                self.items.append({k: " ".join(" ".join(v).split()) for k, v in self.cur.items()})
                self.cur = None

    def handle_data(self, data: str) -> None:
        if self.cur is None:
            return
        active = frozenset().union(*(r for _, r in self.stack))
        if "quote" in active:
            return
        self.cur["all"].append(data)
        for role in ("author", "text", "date"):
            if role in active:
                self.cur[role].append(data)


def forum_messages(
    html: str, selectors: dict[str, str], now: datetime, salt: str, max_chars: int = 600
) -> list[ForumMessage]:
    """Messages of a thread page. ``selectors`` needs at least ``message`` and ``text`` (classes found with
    ``social-check --threads``); without them nothing is read (never a guess)."""
    import hashlib

    if not selectors.get("message") or not selectors.get("text"):
        return []
    c = _Collector(selectors)
    c.feed(html)
    c.close()
    names = sorted({it["author"] for it in c.items if len(it["author"]) >= 3}, key=len, reverse=True)
    out = []
    for it in c.items:
        text = _URL.sub(lambda m: f"[lien {m.group(1).lower()}]", it["text"])
        text = _MENTION.sub("@[membre]", text)
        for name in names:
            text = re.sub(re.escape(name), "[membre]", text, flags=re.I)
        d, ts = message_time(it["date"], now) if it["date"] else (None, None)
        if d is None:
            found = forum_times(it["all"], now)
            d, ts = found[0] if found else (None, None)
        author = it["author"].strip().lower()
        key = hashlib.sha256(f"{salt}|{author}".encode()).hexdigest()[:16] if author else ""
        if text.strip():
            out.append(ForumMessage(key, d, ts, text[:max_chars]))
    return out


def thread_links(html: str, base_url: str, pattern: str | None) -> list[str]:
    """Thread addresses of a forum list page, in page order, without duplicates (``thread_link_regex``)."""
    from urllib.parse import urljoin

    if not pattern:
        return []
    rx = re.compile(pattern)
    out: list[str] = []
    for href in re.findall(r"href=[\"']([^\"']+)[\"']", html):
        if rx.search(href):
            u = urljoin(base_url, href.split("#")[0])
            if u not in out:
                out.append(u)
    return out


def link_patterns(html: str, limit: int = 12) -> list[tuple[str, int]]:
    """Diagnostics: same-site link shapes of a page (digits → N), most frequent first — no text."""
    from collections import Counter
    from urllib.parse import urlparse

    shapes: Counter[str] = Counter()
    for href in re.findall(r"href=[\"']([^\"']+)[\"']", html):
        u = urlparse(href)
        if u.netloc and "boursorama" not in u.netloc:
            continue
        shapes[re.sub(r"\d+", "N", u.path)] += 1
    return shapes.most_common(limit)


class _Shapes(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[list[object]] = []  # [tag.class, text length]
        self.seen: dict[str, list[int]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            return
        cls = next((v or "" for k, v in attrs if k == "class"), "").strip()
        self.stack.append([f"{tag}.{cls}" if cls else "", 0])

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if str(self.stack[i][0]).split(".")[0] == tag or (not self.stack[i][0] and i == len(self.stack) - 1):
                for key, n in self.stack[i:]:
                    if key:
                        self.seen.setdefault(str(key), []).append(int(n))  # type: ignore[call-overload]
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        n = len(data.strip())
        for el in self.stack:
            el[1] = int(el[1]) + n  # type: ignore[call-overload]


def repeated_classes(html: str, min_count: int = 3, limit: int = 15) -> list[tuple[str, int, int]]:
    """Diagnostics: elements whose class repeats on a thread page — (tag.class, count, median text length).
    The message, author and text elements are among them; only markup names and lengths are returned."""
    import statistics

    p = _Shapes()
    p.feed(html)
    p.close()
    rows = [(k, len(v), int(statistics.median(v))) for k, v in p.seen.items() if len(v) >= min_count]
    return sorted(rows, key=lambda r: (-r[1], r[0]))[:limit]


def _shape(text: str) -> str:
    return re.sub(r"[^\W\d_]", "x", re.sub(r"\d", "9", text))


def short_field_shapes(html: str, max_len: int = 20, min_count: int = 5) -> list[tuple[str, int, list[str]]]:
    """Diagnostics: for the short repeated fields of a page (dates, times, counters), the distinct shapes of
    their text with every digit shown as 9 and every letter as x (« 99:99 », « 99/99/9999 », « xxxx ») — a
    format, never a word: a pseudonym becomes « xxxxxxx99 »."""
    from collections import Counter

    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, list[str]]] = []
            self.out: dict[str, list[str]] = {}

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in _VOID:
                return
            cls = next((v or "" for k, v in attrs if k == "class"), "").strip()
            self.stack.append((f"{tag}.{cls}" if cls else tag, []))

        def handle_endtag(self, tag: str) -> None:
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0].split(".")[0] == tag:
                    for key, parts in self.stack[i:]:
                        text = " ".join(" ".join(parts).split())
                        if "." in key and text and len(text) <= max_len:
                            self.out.setdefault(key, []).append(text)
                    del self.stack[i:]
                    return

        def handle_data(self, data: str) -> None:
            for _, parts in self.stack:
                parts.append(data)

    p = _P()
    p.feed(html)
    p.close()
    rows = []
    for key, texts in p.out.items():
        if len(texts) >= min_count:
            shapes = [s for s, _ in Counter(_shape(x) for x in texts).most_common(4)]
            rows.append((key, len(texts), shapes))
    return sorted(rows, key=lambda r: -r[1])


SITE_CHROME = ("navigation", "menu", "label-item", "c-icon", "u-sr-only", "list-link", "o-list-inline")


def is_site_chrome(key: str) -> bool:
    """Menus, navigation and icons repeated on every page of the site: hidden from the diagnostics."""
    return any(x in key for x in SITE_CHROME)


def ancestors_of(html: str, token: str, depth: int = 5) -> list[tuple[str, int]]:
    """Diagnostics: the chains of classed ancestors (nearest first) of the elements whose class contains
    ``token`` — e.g. which block holds each message time. Markup names only."""
    from collections import Counter

    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[str] = []
            self.chains: Counter[str] = Counter()

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in _VOID:
                return
            cls = next((v or "" for k, v in attrs if k == "class"), "").strip()
            if token in cls:
                up = [k for k in reversed(self.stack) if "." in k][:depth]
                self.chains[" < ".join(k[:60] for k in up)] += 1
            self.stack.append(f"{tag}.{cls}" if cls else tag)

        def handle_endtag(self, tag: str) -> None:
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i].split(".")[0] == tag:
                    del self.stack[i:]
                    return

    p = _P()
    p.feed(html)
    p.close()
    return p.chains.most_common(3)
