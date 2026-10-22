"""docs/23 §23.4 — feeds announced by a site's page (``<link rel="alternate">`` and RSS-looking links),
so that a feed address is read from the publisher, never guessed."""

from __future__ import annotations

import re
from urllib.parse import urljoin

_LINK = re.compile(r"<link\b[^>]*>", re.I)
_A = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_ATTR = re.compile(r"(\w[\w-]*)\s*=\s*[\"']([^\"']*)[\"']")
_FEEDISH = re.compile(r"(rss|atom|/feed|feeds?\.|\.xml\b|flux)", re.I)


def discover_feeds(html: str, base_url: str, limit: int = 20) -> list[tuple[str, str]]:
    """(url, label) of the feeds a page announces: declared alternates first, then links whose
    address or label looks like a feed. Relative addresses are resolved against ``base_url``."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(href: str, label: str) -> None:
        url = urljoin(base_url, href.strip())
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append((url, " ".join(label.split())[:80]))

    for tag in _LINK.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        if "alternate" in attrs.get("rel", "").lower() and re.search(r"(rss|atom)", attrs.get("type", ""), re.I):
            add(attrs.get("href", ""), attrs.get("title", "flux déclaré"))
    for href, inner in _A.findall(html):
        label = re.sub(r"<[^>]+>", " ", inner)
        if _FEEDISH.search(href) or re.search(r"\b(rss|flux)\b", label, re.I):
            add(href, label or "lien")
    return out[:limit]


ITUNES = "https://itunes.apple.com"


def podcast_feeds(
    client, apple_id: str | None = None, term: str | None = None, country: str = "fr"
) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    """(feed URL, « show — publisher ») from Apple's public podcast directory (iTunes Lookup / Search
    API): by identifier first, then by search words. The feed address is the publisher's own."""
    found: list[tuple[str, str]] = []
    if apple_id:
        r = client.get(f"{ITUNES}/lookup", params={"id": apple_id, "entity": "podcast"})
        if r.status_code < 400:
            found += [
                (x["feedUrl"], f"{x.get('collectionName', '?')} — {x.get('artistName', '?')} (id {apple_id})")
                for x in r.json().get("results", [])
                if x.get("feedUrl")
            ]
    if not found and term:
        r = client.get(f"{ITUNES}/search", params={"term": term, "media": "podcast", "country": country, "limit": 5})
        if r.status_code < 400:
            found += [
                (
                    x["feedUrl"],
                    f"{x.get('collectionName', '?')} — {x.get('artistName', '?')} (id {x.get('collectionId')})",
                )
                for x in r.json().get("results", [])
                if x.get("feedUrl")
            ]
    return found
