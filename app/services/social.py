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


def forum_ticker(inst: Instrument) -> str | None:
    """The value's ticker for the forum address: local code, else the EODHD code, else the TradingView one."""
    for raw, sep in ((inst.ticker_local, "."), (inst.ticker_eodhd, "."), (inst.ticker_tv, ":")):
        if raw:
            code = raw.split(sep)[-1] if sep == ":" else raw.split(sep)[0]
            if code:
                return code.upper()
    return None


def forum_url(inst: Instrument, templates: dict[str, str]) -> str | None:
    tpl = templates.get(inst.mic or "") or templates.get(inst.exchange or "")
    code = forum_ticker(inst)
    return tpl.format(ticker=code) if tpl and code else None


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
        url = forum_url(inst, templates)
        if url is None:
            continue
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


# --- intraday watch (docs/26 S3, reactivity: the signal must be caught before the market moves) ---------


def _recent_times_forum(
    s: Session, config: LoadedConfig, client: httpx.Client, inst: Instrument, now: datetime
) -> list[float]:
    p = social_params(config).get("boursorama") or {}
    url = forum_url(inst, p.get("url_template_by_mic") or {})
    if not p.get("enabled") or url is None:
        return []
    region = re.compile(p["region_regex"], re.S) if p.get("region_regex") else None
    html = social.boursorama_page(url, client)
    block = region.search(html).group(0) if region and region.search(html) else html
    return [ts.timestamp() for _, ts in social.forum_times(block, now.astimezone(PARIS)) if ts is not None]


def _reddit_times(s: Session, config: LoadedConfig, client: httpx.Client) -> dict[str, list[float]]:
    p = social_params(config).get("reddit") or {}
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not p.get("enabled") or not cid or not secret:
        return {}
    from app.services.news import alias_index, attach_instruments

    aliases = alias_index(s)
    token = social.reddit_token(cid, secret, client)
    out: dict[str, list[float]] = defaultdict(list)
    for sub in p.get("subreddits") or []:
        for kind in ("new", "comments"):
            try:
                for ts, text in social.reddit_listing(str(sub), kind, token, client):
                    for isin in attach_instruments(text, aliases):  # text dropped right after matching
                        out[isin].append(ts.timestamp())
            except Exception as exc:  # noqa: BLE001
                log.warning("Reddit r/%s : %s", sub, exc.__class__.__name__)
    return out


def watch(s: Session, config: LoadedConfig, client: httpx.Client, now: datetime | None = None) -> list[Any]:
    """One pass of the intraday watch: message velocity (last ``window_hours`` vs the usual rate) on the
    followed values, no published news, price not moved yet → « buzz précoce » labelled now (new labels
    are returned for the alert and the targeted scan). Needs no history: one page read is enough."""
    from app.db.models import NewsItem, PriceEod, WeakSignalHit
    from app.domain.weak_signals import BUZZ_EARLY, PUMP, RadarInput, label, velocity
    from app.services.weak_signals import settings as radar_settings

    now = now or datetime.now(UTC)
    w = social_params(config).get("watch") or {}
    if not w.get("enabled", True):
        return []
    st = radar_settings(config)
    rp = st.params
    day = now.astimezone(PARIS).date()
    win, base = float(w.get("window_hours", 2)), float(w.get("base_hours", 24))
    try:
        reddit = _reddit_times(s, config, client)
    except Exception as exc:  # noqa: BLE001
        log.warning("Reddit : %s", exc.__class__.__name__)
        reddit = {}
    news_since = now - timedelta(hours=st.news_hours)
    news: dict[str, int] = defaultdict(int)
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= news_since)):
        for isin in n.isins or []:
            news[isin] += 1
    new_hits = []
    for inst in followed_values(s, int(w.get("max_values", 60)), day):
        times = list(reddit.get(inst.isin, []))
        try:
            times += _recent_times_forum(s, config, client, inst, now)
        except Exception as exc:  # noqa: BLE001 — one page failing never stops the pass
            log.info("forum %s : %s", inst.isin, exc.__class__.__name__)
        finally:
            time.sleep(float((social_params(config).get("boursorama") or {}).get("interval_s", 2.0)))
        hist = list(
            s.scalars(
                select(AttentionDaily.value).where(
                    AttentionDaily.isin == inst.isin,
                    AttentionDaily.source != "wikipedia",
                    AttentionDaily.day >= day - timedelta(days=8),
                    AttentionDaily.day < day,
                )
            )
        )
        usual = sum(hist) / (7 * 24) if hist else None  # messages per hour over the last week (all sources)
        recent, ratio = velocity(times, now.timestamp(), win, base, usual)
        if ratio is None:
            continue
        bars = list(
            s.scalars(select(PriceEod).where(PriceEod.isin == inst.isin).order_by(PriceEod.day.desc()).limit(61))
        )[::-1]
        feats = s.scalar(select(FeatureDaily).where(FeatureDaily.isin == inst.isin).order_by(FeatureDaily.day.desc()))
        move5 = abs(bars[-1].close - bars[-6].close) / feats.atr14 if len(bars) >= 6 and feats and feats.atr14 else None
        from app.domain.weak_signals import relative_volume

        rvol = relative_volume([float(b.volume) if b.volume else None for b in bars])
        lab = label(
            RadarInput(None, rvol, None, news.get(inst.isin, 0), bool(inst.small_cap),
                       feats.rs_rank if feats else None, None, move5, ratio, recent), rp,
        )  # fmt: skip
        if lab is None or lab.label not in (BUZZ_EARLY, PUMP):
            continue
        hit = s.scalar(select(WeakSignalHit).where(WeakSignalHit.isin == inst.isin, WeakSignalHit.day == day))
        if hit is not None and hit.label == lab.label:
            continue  # already seen today: one alert per value and day
        hit = hit or WeakSignalHit(isin=inst.isin, day=day, computed_at=now)
        hit.label, hit.idea, hit.text, hit.rvol, hit.news_recent = (
            lab.label,
            lab.idea,
            lab.text[:300],
            rvol,
            news.get(inst.isin, 0),
        )
        hit.close, hit.computed_at = (bars[-1].close if bars else None), now
        hit.details = {
            "veille_intraday": True,
            "messages_recents": recent,
            "rythme": None if ratio == float("inf") else round(ratio, 2),
            "fenetre_heures": win,
            "sources": [x for x, ok in (("forum", True), ("reddit", inst.isin in reddit)) if ok],
            "releve_a": now.isoformat(timespec="minutes"),
        }
        s.add(hit)
        new_hits.append(hit)
    s.flush()
    return new_hits


# --- backfill: a baseline from day one (older forum pages, Reddit search) ------------------------------


def _store_days(s: Session, isin: str, source: str, days: list[date], first: date, last: date, now: datetime) -> int:
    """Daily counts from message dates, with true zeros on every covered day without a message."""
    by: dict[date, int] = defaultdict(int)
    for d in days:
        by[d] += 1
    n, d = 0, first
    while d <= last:
        _store(s, isin, d, source, by.get(d, 0), now)
        n += 1
        d += timedelta(days=1)
    return n


def backfill(
    s: Session, config: LoadedConfig, client: httpx.Client, day: date, isins: list[str] | None = None, days: int = 60
) -> list[str]:
    """Rebuild ``days`` days of counts for the followed values (or ``isins``) so that the z-score has a
    baseline at once. The oldest day of a source is the oldest message read (never extrapolated)."""
    sp = social_params(config)
    now = datetime.now(UTC)
    values = (
        list(s.scalars(select(Instrument).where(Instrument.isin.in_(isins))))
        if isins
        else followed_values(s, int((sp.get("watch") or {}).get("backfill_values", 60)), day)
    )
    notes = []
    bp = sp.get("boursorama") or {}
    tpl, suffix = bp.get("url_template_by_mic") or {}, bp.get("page_suffix_template") or "page-{n}"
    if bp.get("enabled"):
        n_ok = 0
        for inst in values:
            url = forum_url(inst, tpl)
            if url is None:
                continue
            dates: list[date] = []
            for k in range(1, int(bp.get("backfill_max_pages", 15)) + 1):
                try:
                    html = social.boursorama_page(url if k == 1 else url.rstrip("/") + "/" + suffix.format(n=k), client)
                except Exception:  # noqa: BLE001 — stop at the first page that fails
                    break
                finally:
                    time.sleep(float(bp.get("interval_s", 2.0)))
                page = social.forum_dates(html, day)
                if not page:
                    break
                dates += page
                if min(page) <= day - timedelta(days=days):
                    break
            if dates:
                first = max(min(dates), day - timedelta(days=days))
                _store_days(s, inst.isin, "forum_boursorama", [d for d in dates if d >= first], first, day, now)
                n_ok += 1
        notes.append(f"forum : {n_ok} valeur(s) rattrapée(s)")
    rp = sp.get("reddit") or {}
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if rp.get("enabled") and cid and secret:
        token = social.reddit_token(cid, secret, client)
        n_ok = 0
        for inst in values:
            if not inst.name or len(inst.name) < 4:
                continue
            stamps: list[datetime] = []
            for sub in rp.get("subreddits") or []:
                try:
                    stamps += social.reddit_search(str(sub), f'"{inst.name}"', token, client)
                except Exception:  # noqa: BLE001
                    continue
                finally:
                    time.sleep(float(rp.get("interval_s", 1.0)))
            first = day - timedelta(days=days)
            _store_days(
                s,
                inst.isin,
                "reddit",
                [x.astimezone(PARIS).date() for x in stamps if x.astimezone(PARIS).date() >= first],
                first,
                day - timedelta(days=1),
                now,
            )  # noqa: E501
            n_ok += 1
        notes.append(f"Reddit : {n_ok} valeur(s) rattrapée(s)")
    s.flush()
    return notes
