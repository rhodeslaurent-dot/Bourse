"""docs/26 S3 — daily retail-attention counts per value from forums and social networks, stored in
``attention_daily`` (source ``forum_boursorama``, ``reddit``, ``bluesky``). Counts only: no text, no author.
Values covered: positions, active watchlist, the day's proposals, then the strongest relative strength,
up to ``max_values`` per source (politeness: one request every ``interval_s`` seconds)."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.providers import social
from app.db.models import AttentionDaily, FeatureDaily, Instrument, PositionRow, Proposal, WatchlistRow
from app.domain.timeutil import PARIS

log = logging.getLogger("bourse.social")


def social_params(config: LoadedConfig) -> dict[str, Any]:
    return (((config.raw or {}).get("weak_signals") or {}).get("social")) or {}


def followed_values(s: Session, limit: int, day: date) -> list[Instrument]:
    """Positions, watchlist and the day's proposals first, then the best relative strength of the universe."""
    order: list[str] = []
    for isin in s.scalars(select(PositionRow.isin).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0)):
        order.append(isin)
    for isin in s.scalars(select(WatchlistRow.isin).where(WatchlistRow.active.is_(True))):
        order.append(isin)
    start = datetime.combine(day - timedelta(days=3), datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    for isin in s.scalars(select(Proposal.isin).where(Proposal.created_at >= start, Proposal.deleted_at.is_(None))):
        order.append(isin)
    latest = s.scalar(select(FeatureDaily.day).order_by(FeatureDaily.day.desc()))
    if latest is not None:
        for isin in s.scalars(
            select(FeatureDaily.isin).where(FeatureDaily.day == latest).order_by(FeatureDaily.rs_rank.desc())
        ):
            order.append(isin)
    seen: list[str] = list(dict.fromkeys(order))
    insts = {
        i.isin: i for i in s.scalars(select(Instrument).where(Instrument.isin.in_(seen), Instrument.active.is_(True)))
    }
    return [insts[i] for i in seen if i in insts][:limit]


def _store(s: Session, isin: str, day: date, source: str, value: int, now: datetime) -> None:
    row = s.scalar(
        select(AttentionDaily).where(
            AttentionDaily.isin == isin, AttentionDaily.day == day, AttentionDaily.source == source
        )
    )
    if row is None:
        s.add(AttentionDaily(isin=isin, day=day, source=source, lang="-", value=value, received_at=now))
    elif value > row.value:  # a later read of the same day only raises the count (floor semantics)
        row.value, row.received_at = value, now


def collect_forum(s: Session, config: LoadedConfig, client: httpx.Client, day: date) -> tuple[int, list[str]]:
    p = social_params(config).get("boursorama") or {}
    if not p.get("enabled"):
        return 0, []
    templates: dict[str, str] = p.get("url_template_by_mic") or {}
    region = re.compile(p["region_regex"], re.S) if p.get("region_regex") else None
    now = datetime.now(UTC)
    n, errors = 0, []
    for inst in followed_values(s, int(p.get("max_values", 200)), day):
        tpl = templates.get(inst.mic)
        if not tpl or not inst.ticker_local:
            continue
        url = tpl.format(ticker=inst.ticker_local.split(".")[0].upper())
        try:
            html = social.boursorama_page(url, client)
        except Exception as exc:  # noqa: BLE001 — one page failing never stops the others
            errors.append(f"{inst.isin}: {exc.__class__.__name__}")
            continue
        finally:
            time.sleep(float(p.get("interval_s", 2.0)))
        block = region.search(html).group(0) if region and region.search(html) else html
        dates = social.forum_dates(block, day)
        counts = {c.day: c.messages for c in social.daily_counts(dates)}
        for d in (day - timedelta(days=1), day):
            if d in counts:
                _store(s, inst.isin, d, "forum_boursorama", counts[d], now)
                n += 1
            elif dates and min(dates) < d:  # the page goes back before that day: no message is a true zero
                _store(s, inst.isin, d, "forum_boursorama", 0, now)
    s.flush()
    return n, errors


def collect_reddit(s: Session, config: LoadedConfig, client: httpx.Client, day: date) -> tuple[int, list[str]]:
    p = social_params(config).get("reddit") or {}
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not p.get("enabled") or not cid or not secret:
        return 0, [] if not p.get("enabled") else ["identifiants Reddit absents (env-setup reddit)"]
    from app.services.news import alias_index, attach_instruments

    aliases = alias_index(s)
    token = social.reddit_token(cid, secret, client)
    counts: dict[tuple[str, date], int] = defaultdict(int)
    errors = []
    covered_from: datetime | None = None  # the most recent « oldest item » over the listings
    for sub in p.get("subreddits") or []:
        for kind in ("new", "comments"):
            try:
                items = social.reddit_listing(str(sub), kind, token, client)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"r/{sub}: {exc.__class__.__name__}")
                continue
            if items:
                oldest = min(ts for ts, _ in items)
                covered_from = oldest if covered_from is None else max(covered_from, oldest)
            for ts, text in items:
                d = ts.astimezone(PARIS).date()
                if d < day - timedelta(days=2):
                    continue
                for isin in attach_instruments(text, aliases):  # the text is dropped right after matching
                    counts[(isin, d)] += 1
            time.sleep(float(p.get("interval_s", 1.0)))
    now = datetime.now(UTC)
    for (isin, d), v in counts.items():
        _store(s, isin, d, "reddit", v, now)
    yday = day - timedelta(days=1)
    yday_start = datetime.combine(yday, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    if covered_from is not None and covered_from <= yday_start:  # yesterday fully read: unnamed values = 0
        named = {isin for (isin, d) in counts if d == yday}
        for inst in followed_values(s, int(p.get("max_values", 400)), day):
            if inst.isin not in named:
                _store(s, inst.isin, yday, "reddit", 0, now)
    s.flush()
    return len(counts), errors


def collect_bluesky(s: Session, config: LoadedConfig, client: httpx.Client, day: date) -> tuple[int, list[str]]:
    p = social_params(config).get("bluesky") or {}
    handle, pw = os.environ.get("BLUESKY_HANDLE"), os.environ.get("BLUESKY_APP_PASSWORD")
    if not p.get("enabled") or not handle or not pw:
        return 0, [] if not p.get("enabled") else ["identifiants Bluesky absents (env-setup bluesky)"]
    token = social.bluesky_session(handle, pw, client)
    since = datetime.combine(day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    now = datetime.now(UTC)
    n, errors = 0, []
    for inst in followed_values(s, int(p.get("max_values", 80)), day):
        query = f'"{inst.name}"' if inst.name and len(inst.name) >= 4 else None
        if query is None:
            continue
        try:
            c = social.bluesky_count(query, since, token, client, float(p.get("interval_s", 0.5)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{inst.isin}: {exc.__class__.__name__}")
            continue
        _store(s, inst.isin, day, "bluesky", c, now)
        n += 1
    s.flush()
    return n, errors


def collect_all(s: Session, config: LoadedConfig, client: httpx.Client, day: date) -> list[str]:
    notes = []
    for name, fn in (("forum", collect_forum), ("Reddit", collect_reddit), ("Bluesky", collect_bluesky)):
        try:
            with s.begin_nested():
                n, errors = fn(s, config, client, day)
            if n or errors:
                notes.append(f"{name} {n}" + (f" ({len(errors)} échec(s))" if errors else ""))
        except Exception as exc:  # noqa: BLE001 — a network failing never stops the radar
            log.warning("%s : %s", name, exc.__class__.__name__)
            notes.append(f"{name} en échec ({exc.__class__.__name__})")
    return notes
