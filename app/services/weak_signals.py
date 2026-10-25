"""docs/26 — weak signals radar: Wikidata mapping, Wikipedia daily views, internal volume and press
coverage → one label per value and day (``weak_signal_hits``). Never a trigger: the engine reads the
label as a caution (or a block for an unconfirmed frenzy), the report lists the ideas to examine."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.providers import wikimedia
from app.db.models import AttentionDaily, FeatureDaily, Instrument, NewsItem, PriceEod, WeakSignalHit, WikiArticle
from app.domain.backtest import summarize
from app.domain.timeutil import PARIS
from app.domain.weak_signals import PUMP, RadarInput, RadarParams, label, relative_volume, robust_z, social_z

log = logging.getLogger("bourse.weak_signals")

# local Wikipedia per place; English is always read (docs/26 §26.7)
DEFAULT_LANGS = {
    "XPAR": ["fr"], "XBRU": ["fr", "nl"], "XAMS": ["nl"], "XETR": ["de"], "XMIL": ["it"], "XMAD": ["es"],
    "XSTO": ["sv"], "XCSE": ["da"], "XHEL": ["fi"], "XOSL": ["no"], "XLIS": ["pt"], "XWBO": ["de"],
    "XDUB": [], "XLON": [], "XSWX": ["de", "fr"],
}  # fmt: skip


@dataclass
class RadarSettings:
    enabled: bool = True
    params: RadarParams = field(default_factory=RadarParams)
    langs_by_mic: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_LANGS))
    user_agent: str = wikimedia.DEFAULT_UA
    news_hours: int = 48
    max_ideas: int = 5
    request_interval_s: float = 0.05
    first_days: int = 90
    remap_days: int = 30


def settings(config: LoadedConfig) -> RadarSettings:
    raw = (config.raw or {}).get("weak_signals") or {}
    p = RadarParams(
        **{k: type(getattr(RadarParams(), k))(raw[k]) for k in RadarParams.__dataclass_fields__ if k in raw}
    )
    st = RadarSettings(params=p)
    for k in ("enabled", "user_agent", "news_hours", "max_ideas", "request_interval_s", "first_days", "remap_days"):
        if k in raw:
            setattr(st, k, type(getattr(st, k))(raw[k]))
    if raw.get("wiki_languages"):
        st.langs_by_mic.update({str(k): list(v or []) for k, v in raw["wiki_languages"].items()})
    return st


def languages(inst: Instrument, st: RadarSettings) -> list[str]:
    return list(dict.fromkeys([*st.langs_by_mic.get(inst.mic, []), "en"]))


def _universe(s: Session, isins: list[str] | None = None) -> list[Instrument]:
    q = select(Instrument).where(Instrument.active.is_(True))
    q = q.where(Instrument.isin.in_(isins)) if isins else q.where(Instrument.in_universe.is_(True))
    return list(s.scalars(q))


def refresh_articles(s: Session, config: LoadedConfig, client: httpx.Client, isins: list[str] | None = None) -> int:
    """Wikidata lookup for values never checked (or checked more than ``remap_days`` ago). Returns rows written."""
    st = settings(config)
    now = datetime.now(UTC)
    known: dict[tuple[str, str], WikiArticle] = {(a.isin, a.lang): a for a in s.scalars(select(WikiArticle))}
    todo: dict[str, list[str]] = {}
    for inst in _universe(s, isins):
        for lg in languages(inst, st):
            a = known.get((inst.isin, lg))
            checked = (
                a.checked_at.replace(tzinfo=UTC) if a and a.checked_at.tzinfo is None else (a.checked_at if a else None)
            )
            if checked is None or now - checked > timedelta(days=st.remap_days):
                todo.setdefault(inst.isin, []).append(lg)
    if not todo:
        return 0
    langs = sorted({lg for v in todo.values() for lg in v})
    found = wikimedia.wikidata_titles(list(todo), langs, client, st.user_agent)
    n = 0
    for isin, lgs in todo.items():
        for lg in lgs:
            a = known.get((isin, lg)) or WikiArticle(isin=isin, lang=lg, checked_at=now)
            a.title, a.checked_at = (found.get(isin) or {}).get(lg), now
            s.add(a)
            n += 1
    s.flush()
    return n


def refresh_attention(s: Session, config: LoadedConfig, client: httpx.Client, day: date) -> tuple[int, list[str]]:
    """Views up to ``day`` for each mapped article: ``first_days`` the first time, then since the last day stored."""
    st = settings(config)
    rows, errors = 0, []
    last: dict[tuple[str, str], date] = {
        (r.isin, r.lang): r.d
        for r in s.execute(
            select(AttentionDaily.isin, AttentionDaily.lang, func.max(AttentionDaily.day).label("d")).group_by(
                AttentionDaily.isin, AttentionDaily.lang
            )
        )
    }
    universe = {i.isin for i in _universe(s)}
    for a in s.scalars(select(WikiArticle).where(WikiArticle.title.is_not(None))):
        if a.isin not in universe or a.title is None:
            continue
        start = (
            last[(a.isin, a.lang)] + timedelta(days=1) if (a.isin, a.lang) in last else day - timedelta(st.first_days)
        )
        if start > day:
            continue
        try:
            pv = wikimedia.pageviews(a.lang, a.title, start, day, client, st.user_agent)
        except Exception as exc:  # noqa: BLE001 — one article failing never stops the others
            errors.append(f"{a.isin}/{a.lang}: {exc.__class__.__name__}")
            continue
        finally:
            time.sleep(st.request_interval_s)
        for d, v in (pv.views if pv else {}).items():
            s.add(
                AttentionDaily(isin=a.isin, day=d, source="wikipedia", lang=a.lang, value=v, received_at=pv.received_at)
            )
            rows += 1
        s.flush()
    return rows, errors


def _attention_z(s: Session, isin: str, day: date, p: RadarParams) -> tuple[float | None, dict[str, Any]]:
    """Highest per-language z; a series whose last day is more than 2 days old is not used (stale)."""
    since = day - timedelta(days=p.baseline_days + p.recent_days + 10)
    series: dict[str, dict[date, int]] = defaultdict(dict)
    for r in s.scalars(
        select(AttentionDaily).where(
            AttentionDaily.isin == isin,
            AttentionDaily.day > since,
            AttentionDaily.day <= day,
            AttentionDaily.source == "wikipedia",
        )
    ):
        series[r.lang][r.day] = r.value
    best: float | None = None
    per: dict[str, Any] = {}
    for lg, pts in series.items():
        days = sorted(pts)
        if not days or (day - days[-1]).days > 2:
            per[lg] = "périmé"
            continue
        z = robust_z([float(pts[d]) for d in days], p)
        per[lg] = None if z is None else round(z, 2)
        if z is not None and (best is None or z > best):
            best = z
    return best, per


def _social_z(s: Session, isin: str, day: date, p: RadarParams) -> tuple[float | None, dict[str, Any]]:
    """Highest z over the forum / social sources (each against its own baseline; docs/26 S3)."""
    since = day - timedelta(days=p.baseline_days + p.recent_days + 10)
    series: dict[str, dict[date, int]] = defaultdict(dict)
    for r in s.scalars(
        select(AttentionDaily).where(
            AttentionDaily.isin == isin,
            AttentionDaily.day > since,
            AttentionDaily.day <= day,
            AttentionDaily.source != "wikipedia",
        )
    ):
        series[r.source][r.day] = r.value
    best: float | None = None
    per: dict[str, Any] = {}
    for src, pts in series.items():
        days = sorted(pts)
        if not days or (day - days[-1]).days > 3:
            per[src] = "périmé"
            continue
        z = social_z([float(pts[d]) for d in days], p)
        per[src] = None if z is None else round(z, 2)
        if z is not None and (best is None or z > best):
            best = z
    return best, per


def compute_hits(s: Session, config: LoadedConfig, day: date, now: datetime | None = None) -> list[WeakSignalHit]:
    """Labels of the last session on or before ``day``. Each label is dated on **its session** (the bar
    used), and the news window runs from the eve of that session to now — a Friday release still counts
    on Monday morning. Bars older than 4 days (stale EOD) are not labelled."""
    st = settings(config)
    p = st.params
    now = now or datetime.now(UTC)
    since = datetime.combine(day - timedelta(days=6), datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    news_ts: dict[str, list[datetime]] = defaultdict(list)
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= since)):
        ts = n.published_at or n.fetched_at
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
        for isin in n.isins or []:
            news_ts[isin].append(ts)
    out: list[WeakSignalHit] = []
    for inst in _universe(s):
        bars = list(
            s.scalars(
                select(PriceEod)
                .where(PriceEod.isin == inst.isin, PriceEod.day <= day)
                .order_by(PriceEod.day.desc())
                .limit(61)
            )
        )[::-1]
        if not bars or (day - bars[-1].day).days > 4:
            continue
        session = bars[-1].day
        eve = datetime.combine(session - timedelta(days=1), datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
        window_start = min(eve, now - timedelta(hours=st.news_hours))
        n_news = sum(1 for ts in news_ts.get(inst.isin, []) if window_start <= ts <= now)
        feats = s.scalar(
            select(FeatureDaily)
            .where(FeatureDaily.isin == inst.isin, FeatureDaily.day <= session)
            .order_by(FeatureDaily.day.desc())
        )
        rvol = relative_volume([float(b.volume) if b.volume else None for b in bars])
        move = (
            abs(bars[-1].close - bars[-2].close) / feats.atr14
            if len(bars) >= 2 and feats is not None and feats.atr14
            else None
        )
        z, per = _attention_z(s, inst.isin, max(day, session), p)
        sz, per_social = _social_z(s, inst.isin, max(day, session), p)
        move5 = (
            abs(bars[-1].close - bars[-6].close) / feats.atr14
            if len(bars) >= 6 and feats is not None and feats.atr14
            else None
        )
        lab = label(
            RadarInput(z, rvol, move, n_news, bool(inst.small_cap), feats.rs_rank if feats else None, sz, move5), p
        )
        hit = s.scalar(select(WeakSignalHit).where(WeakSignalHit.isin == inst.isin, WeakSignalHit.day == session))
        if hit is not None and (hit.details or {}).get("veille_intraday"):
            continue  # a label of the intraday watch is kept as recorded (docs/26 S3)
        if lab is None:
            if hit is not None:
                s.delete(hit)  # a recomputation that no longer labels the session removes the old label
            continue
        hit = hit or WeakSignalHit(isin=inst.isin, day=session, computed_at=now)
        hit.label, hit.idea, hit.text = lab.label, lab.idea, lab.text[:300]
        hit.attention_z, hit.rvol, hit.news_recent = z, rvol, n_news
        hit.close, hit.computed_at = bars[-1].close, now
        hit.details = {
            "z_par_langue": per,
            "z_forums_reseaux": per_social,
            "mouvement_atr": None if move is None else round(move, 2),
            "force_relative": feats.rs_rank if feats else None,
            "source_attention": "Wikipédia (Wikimedia REST), consultations quotidiennes",
            "cours_du": session.isoformat(),
        }
        s.add(hit)
        out.append(hit)
    s.flush()
    return out


def latest(s: Session, isin: str, day: date, max_age_days: int = 4) -> WeakSignalHit | None:
    return s.scalar(
        select(WeakSignalHit)
        .where(
            WeakSignalHit.isin == isin,
            WeakSignalHit.day <= day,
            WeakSignalHit.day >= day - timedelta(days=max_age_days),
        )
        .order_by(WeakSignalHit.day.desc())
    )


def is_pump(hit: WeakSignalHit | None) -> bool:
    return hit is not None and hit.label == PUMP


def last_hit_day(s: Session, day: date) -> date | None:
    """Session of the latest labels computed on or before ``day`` (labels are dated on their session)."""
    return s.scalar(
        select(func.max(WeakSignalHit.day)).where(
            WeakSignalHit.day <= day, WeakSignalHit.day >= day - timedelta(days=4)
        )
    )


def ideas(s: Session, config: LoadedConfig, day: date, exclude: set[str]) -> list[WeakSignalHit]:
    """Corroborated attention on strong values without a proposal of the day: to examine, never to buy as is."""
    st = settings(config)
    d = last_hit_day(s, day)
    if d is None:
        return []
    rows = s.scalars(
        select(WeakSignalHit)
        .where(WeakSignalHit.day == d, WeakSignalHit.idea.is_(True))
        .order_by(WeakSignalHit.attention_z.desc())
    )
    return [h for h in rows if h.isin not in exclude][: st.max_ideas]


def measure(s: Session, horizons: tuple[int, ...] = (5, 20)) -> dict[str, dict[str, Any]]:
    """docs/26 §26.6 — return after each label, in excess of the universe median over the same sessions,
    measured **from the next session's close** (the label is known the morning after its session, so the
    overnight gap is never counted). Only the prices since the first label are loaded."""
    first = s.scalar(select(func.min(WeakSignalHit.day)))
    if first is None:
        return {}
    closes: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for r in s.execute(
        select(PriceEod.isin, PriceEod.day, PriceEod.close).where(PriceEod.day >= first).order_by(PriceEod.day)
    ):
        closes[r.isin].append((r.day, r.close))
    index = {isin: {d: k for k, (d, _) in enumerate(rows)} for isin, rows in closes.items()}

    def fwd(isin: str, d: date, h: int) -> float | None:
        k = index.get(isin, {}).get(d)
        rows = closes.get(isin, [])
        if k is None or k + 1 + h >= len(rows) or rows[k + 1][1] <= 0:
            return None
        return rows[k + 1 + h][1] / rows[k + 1][1] - 1

    medians: dict[tuple[date, int], float | None] = {}

    def median_ret(d: date, h: int) -> float | None:
        if (d, h) not in medians:
            vals = sorted(x for x in (fwd(i, d, h) for i in closes) if x is not None)
            medians[(d, h)] = vals[len(vals) // 2] if len(vals) >= 20 else None
        return medians[(d, h)]

    by: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for hit in s.scalars(select(WeakSignalHit)):
        for h in horizons:
            r, m = fwd(hit.isin, hit.day, h), median_ret(hit.day, h)
            if r is not None and m is not None:
                by[hit.label][h].append(r - m)
    # note: labels of the same day are correlated and 20-session windows overlap — the t is optimistic
    return {lab: {f"{h}_seances": summarize(v).as_dict() for h, v in hs.items()} for lab, hs in by.items()}
