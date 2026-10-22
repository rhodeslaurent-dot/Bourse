"""Press review of a letter (docs/21): the articles linked at the end of a letter (Momentum…) are
read at reception, classified (macro / sector / value, themes) and stored as news items, so that
the letters review, the assistant and the thesis get the macro and micro context of the day.

Same safety as the « Lire la suite » page (docs/19 §19.6): allow-listed destinations only, every
redirect checked, no cookie, one page per link, a pause between pages; a subscriber-only article
keeps its title and lead only. Nothing is invented: an absent publication date is the letter's
reception time, flagged as such."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers.linked_page import fetch_article, press_links
from app.data.providers.rss import NewsItem as RssItem
from app.db.models import NewsItem, NewsletterItem

log = logging.getLogger("bourse.press_review")


def press_rules(config: Any) -> dict[str, dict[str, Any]]:
    imap = config.params.data.imap or {}
    rules = imap.get("press_review") or {}
    return {k: v for k, v in rules.items() if isinstance(v, dict) and v.get("allowed_domains")}


def _published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def ingest_press_review(
    s: Session,
    html: str,
    item: NewsletterItem,
    rule: dict[str, Any],
    gateway: Any | None,
    client: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    from app.services.news import ingest_rss_items

    links = press_links(
        html,
        list(rule.get("start_after") or ["Revue de presse"]),
        list(rule.get("exclude_texts") or []),
        int(rule.get("max_links", 10)),
    )
    out: list[dict[str, Any]] = []
    received = item.received_at if item.received_at.tzinfo else item.received_at.replace(tzinfo=UTC)
    for k, (href, label) in enumerate(links):
        if k:
            sleep(float(rule.get("min_interval_s", 3)))
        page, meta = fetch_article(
            href,
            list(rule.get("allowed_domains") or []),
            list(rule.get("tracking_domains") or []),
            client=client,
            timeout=float(rule.get("timeout_s", 15)),
        )
        entry: dict[str, Any] = {
            "lien": label[:120],
            "url": page.url or href,
            "statut": page.status,
            "detail": page.detail,
        }
        if page.status not in ("ok", "reserve") or not page.url:
            out.append(entry)
            continue
        title = (meta.get("titre") or label or "").strip()[:300]
        body = page.text if page.status == "ok" else (meta.get("chapo") or "")
        published = _published(meta.get("publie"))
        url_hash = hashlib.sha256(page.url.encode()).hexdigest()
        rss = RssItem(
            source="presse",
            guid=page.url,
            title=title or page.url,
            url=page.url,
            published_at=published or received,
            fetched_at=datetime.now(UTC),
            body=body,
            dedup_hash=url_hash,
        )
        ingest_rss_items(s, [rss], gateway, max_items=1)
        row = s.scalar(select(NewsItem).where(NewsItem.dedup_hash == url_hash))
        if row is not None:
            c = dict(row.classification or {})
            if row.classified_by != "llm" or not c.get("portee"):
                c["portee"] = "valeur" if row.isins else "macro"  # rules fallback (docs/21 §21.5)
            c.update(
                {
                    "editeur": urlparse(page.url).hostname,
                    "date_source": "article" if published else "lettre",
                    "statut_page": page.status,
                    "lettre_id": item.id,
                }
            )
            row.classification = c
            entry.update({"news_id": row.id, "titre": title})
        out.append(entry)
    parsed = dict(item.parsed or {})
    parsed["revue_presse"] = out
    item.parsed = parsed
    s.flush()
    return out


def macro_context(s: Session, now: datetime, days: int, limit: int) -> list[dict[str, Any]]:
    """« Contexte macro (revue de presse) » for the letters review, the assistant and the thesis."""
    since = now - timedelta(days=days)
    rows = s.scalars(
        select(NewsItem)
        .where(NewsItem.source == "presse", NewsItem.fetched_at >= since)
        .order_by(NewsItem.fetched_at.desc())
    )
    out = []
    for n in rows:
        c = n.classification or {}
        if c.get("portee") not in ("macro", "secteur"):
            continue
        out.append(
            {
                "titre": n.title,
                "editeur": c.get("editeur"),
                "date": (n.published_at or n.fetched_at).isoformat(timespec="minutes"),
                "date_source": c.get("date_source"),
                "themes": c.get("themes") or [],
                "resume": c.get("resume"),
                "portee": c.get("portee"),
            }
        )
        if len(out) >= limit:
            break
    return out
