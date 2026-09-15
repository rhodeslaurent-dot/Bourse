"""``news_scan`` (07:00–09:00 every 5 min, then every 15 min until 19:00): RSS feeds of params.yaml
→ ingestion + classification; a feed returning nothing two days in a row → P4 (docs/14 §14.2)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.data.providers.rss import fetch_feed
from app.db.models import DataFreshness
from app.db.session import db_session
from app.notify.base import Event
from app.scheduler.runner import JobContext
from app.services.news import ingest_rss_items

log = logging.getLogger("bourse.jobs.news_scan")


def run(ctx: JobContext) -> int:
    feeds = [f for f in ctx.config.params.data.rss_feeds if f.get("url") and "renseigner" not in str(f.get("url"))]
    max_items = int(((ctx.config.params.detectors or {}).get("d6_catalysts") or {}).get("max_items_per_cycle", 50))
    total = 0
    errors: list[str] = []
    with db_session() as s:
        for f in feeds:
            name = str(f["name"])
            fresh = s.get(DataFreshness, f"rss:{name}") or DataFreshness(source=f"rss:{name}")
            s.add(fresh)
            try:
                items = fetch_feed(name, str(f["url"]))
                rep = ingest_rss_items(s, items, ctx.llm, max_items=max_items)
                total += rep.new
                fresh.last_ok_at = datetime.now(UTC)
                fresh.last_error = None
                if not items:
                    errors.append(f"{name}: flux vide")
            except Exception as exc:  # noqa: BLE001 — one feed failing never stops the others
                fresh.last_error, fresh.last_error_at = f"{exc.__class__.__name__}: {exc}"[:300], datetime.now(UTC)
                errors.append(f"{name}: {exc.__class__.__name__}")
        if errors and ctx.alerts:
            stale = [
                r
                for r in s.scalars(select(DataFreshness).where(DataFreshness.source.like("rss:%")))
                if r.last_error_at
                and (r.last_ok_at is None or (datetime.now(UTC) - r.last_ok_at.replace(tzinfo=UTC)).days >= 2)
            ]
            if stale:
                ctx.alerts.emit(
                    s,
                    Event(
                        "P4",
                        "rss_parser",
                        f"{len(stale)} flux RSS sans résultat depuis 2 jours",
                        [r.source + " : " + (r.last_error or "") for r in stale][:6],
                    ),
                    ctx.mode,
                    False,
                )
    return total
