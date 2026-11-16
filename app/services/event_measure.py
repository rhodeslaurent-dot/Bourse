"""Lot S3 (step 1 of the user's plan, 15/11/2026) — what each value did after each event, measured every evening.

For every journal event (lot S2): the reference is the close of the first session of the value's own venue that ends
after the event became public (one could act at that close); the value's return 1, 5 and 20 sessions later is set
against the median return of the other values of its venue over the same sessions (and of its sector on that venue
when it has enough members). A window crossing a change of scale is ``unavailable``; a window not finished yet is
``pending`` and measured again the next evening. The scoreboard groups the final measures by source, kind and
direction, and splits them by the context stored with each event (regime of the venue, VIX, macro announcement).
"""

from __future__ import annotations

import os
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.domain.event_stats import GroupStats, broken, group_stats, signed, total_return, venue_median
from app.domain.timeutil import PARIS, from_store

HORIZONS = (1, 5, 20)
SECTOR_MIN = 5
VENUE_MIN = 10


@dataclass(frozen=True)
class MeasureSettings:
    horizons: tuple[int, ...] = HORIZONS
    venue_min: int = VENUE_MIN  # values of the venue needed for its median
    sector_min: int = SECTOR_MIN  # values of the same sector on the venue needed for the sector median


def settings(config: Any) -> MeasureSettings:
    """``event_journal.horizons``, ``.venue_min_members``, ``.sector_min_members`` (defaults when absent)."""
    g = (getattr(config, "raw", None) or {}).get("event_journal") or {}
    hz = tuple(sorted({int(h) for h in g.get("horizons", HORIZONS) if int(h) > 0})) or HORIZONS
    return MeasureSettings(hz, int(g.get("venue_min_members", VENUE_MIN)), int(g.get("sector_min_members", SECTOR_MIN)))


@dataclass
class MeasureReport:
    final: int = 0
    pending: int = 0
    unavailable: int = 0
    events: int = 0
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"{self.events} événement(s) mesuré(s) : {self.final} mesure(s) finale(s), {self.pending} en attente, "
                f"{self.unavailable} indisponible(s)")  # fmt: skip


def _bars(s: Session, isin: str, start: date, end: date) -> list[tuple[date, float, float | None]]:
    """The value's closes between two dates, with the adjustment factor ``adj_close / close`` (None when absent)."""
    from app.db.models import PriceEod

    rows = s.execute(
        select(PriceEod.day, PriceEod.close, PriceEod.adj_close)
        .where(PriceEod.isin == isin, PriceEod.day >= start, PriceEod.day <= end)
        .order_by(PriceEod.day)
    )
    return [(d, float(c), (float(a) / float(c)) if a and c else None) for d, c, a in rows]


class _Medians:
    """Returns of every value of a venue between two sessions (and their sector). The closes of a venue's day are
    read once and kept in a bounded cache (a backfill since 2016 walks the days in order, never all at once)."""

    def __init__(self, s: Session, st: MeasureSettings, max_days: int = 400) -> None:
        self.s = s
        self.st = st
        self.max_days = max_days
        self.days: OrderedDict[tuple[str, date], dict[str, tuple[float, float | None, str | None]]] = OrderedDict()

    def day(self, mic: str, d: date) -> dict[str, tuple[float, float | None, str | None]]:
        key = (mic, d)
        if key in self.days:
            self.days.move_to_end(key)
            return self.days[key]
        from app.db.models import Instrument, PriceEod

        rows = self.s.execute(
            select(PriceEod.isin, PriceEod.close, PriceEod.adj_close, Instrument.sector)
            .join(Instrument, Instrument.isin == PriceEod.isin)
            .where(Instrument.mic == mic, Instrument.in_universe.is_(True), PriceEod.day == d)
        )
        out = {isin: (float(c), (float(a) / float(c)) if a else None, sec) for isin, c, a, sec in rows if c}
        self.days[key] = out
        if len(self.days) > self.max_days:
            self.days.popitem(last=False)
        return out

    def medians(
        self, mic: str, sector: str | None, d0: date, d1: date, exclude: str
    ) -> tuple[float | None, float | None]:
        a, b = self.day(mic, d0), self.day(mic, d1)
        w = {
            i: (total_return(ca, fa, b[i][0], b[i][1]), sec)
            for i, (ca, fa, sec) in a.items()
            if i != exclude and i in b and ca > 0 and 0.25 < b[i][0] / ca < 4
        }
        venue = venue_median([r for r, _ in w.values()], self.st.venue_min)
        sec = venue_median([r for r, sc in w.values() if sc == sector], self.st.sector_min) if sector else None
        return venue, sec


def _venue(cals: Any, mic: str | None) -> Any:
    return cals.get(mic) if cals is not None and mic and mic in getattr(cals, "venues", {}) else None


def _close_moment(venue: Any, d: date) -> datetime:
    """When the session of ``d`` closes on the value's venue (17:30 Paris when the venue's calendar is unknown)."""
    if venue is not None:
        try:
            ses = venue.session(d)
            if ses.close_time is not None:
                return datetime.combine(d, ses.close_time, tzinfo=ses.tz)
        except ValueError:  # year not in the calendar: the default close
            pass
    return datetime.combine(d, time(17, 30), tzinfo=PARIS)


def _calendars() -> Any:
    try:
        from app.domain.calendar import load_market_calendars

        return load_market_calendars(os.environ.get("MARKET_CALENDARS_PATH", "config/market_calendars.yaml"))
    except (OSError, KeyError, ValueError):
        return None


def _todo(s: Session, limit: int, n_horizons: int) -> list[Any]:
    """Events with a horizon not measured yet or still pending, oldest first."""
    from sqlalchemy import func

    from app.db.models import EventOutcome, MarketEvent

    done = (
        select(EventOutcome.event_id)
        .where(EventOutcome.status != "pending")
        .group_by(EventOutcome.event_id)
        .having(func.count() >= n_horizons)
    )
    return list(
        s.scalars(select(MarketEvent).where(MarketEvent.id.not_in(done)).order_by(MarketEvent.event_at).limit(limit))
    )


def reset(s: Session) -> int:
    """Forget every measure (16/11/2026: the method changed — returns now include dividends). The outcomes are
    derived from stored closes only, so the next ``measure`` rebuilds them identically from the same data."""
    from sqlalchemy import delete

    from app.db.models import EventOutcome

    n = s.execute(delete(EventOutcome)).rowcount or 0
    s.flush()
    return int(n)


def measure(s: Session, today: date, calendars: Any | None = None, limit: int = 5000,
            now: datetime | None = None, st: MeasureSettings | None = None) -> MeasureReport:  # fmt: skip
    """Measure (or measure again) the ``limit`` oldest events whose horizons are not all final.

    The batch is read **in time order** (16/11/2026, before the first run on the production base of 54 million
    closes): consecutive events share their sessions, so each venue's day of closes is read once and kept in a
    bounded cache; the closes of a value are read once per batch, over the batch's dates only."""
    from app.db.models import EventOutcome, Instrument

    now = now or datetime.now(UTC)
    st = st or MeasureSettings()
    cals = calendars if calendars is not None else _calendars()
    rep = MeasureReport()
    med = _Medians(s, st)
    events = _todo(s, limit, len(st.horizons))
    if not events:
        return rep
    existing = {
        (o.event_id, o.horizon): o
        for k in range(0, len(events), 900)
        for o in s.scalars(select(EventOutcome).where(EventOutcome.event_id.in_([e.id for e in events[k : k + 900]])))
    }
    days = [from_store(e.event_at).date() for e in events]
    lo, hi = min(days) - timedelta(days=7), max(days) + timedelta(days=3 * max(st.horizons) + 40)
    bars: dict[str, list[tuple[date, float, float | None]]] = {}
    sectors: dict[str, str | None] = {}
    for ev in sorted(events, key=lambda e: (from_store(e.event_at), e.id)):
        if ev.isin not in bars:
            bars[ev.isin] = _bars(s, ev.isin, lo, hi)
            inst = s.get(Instrument, ev.isin)
            sectors[ev.isin] = inst.sector if inst is not None else None
        rep.events += 1
        _measure_one(ev, bars[ev.isin], sectors[ev.isin], today, now, cals, med, st, existing, rep, s, EventOutcome)
        if rep.events % 2000 == 0:
            s.flush()
    s.flush()
    return rep


def _measure_one(ev: Any, b: list[tuple[date, float, float | None]], sector: str | None, today: date, now: datetime,
                 cals: Any, med: _Medians, st: MeasureSettings, existing: dict[tuple[int, int], Any],
                 rep: MeasureReport, s: Session, outcome_cls: Any) -> None:  # fmt: skip
    at = from_store(ev.event_at)
    venue = _venue(cals, ev.mic)
    local = at.astimezone(venue.tz if venue is not None else PARIS).date()
    i = next((k for k, (d, _, _) in enumerate(b) if d >= local), None)
    if i is not None and b[i][0] == local and _close_moment(venue, local) <= at:
        i = i + 1 if i + 1 < len(b) else None
    for h in st.horizons:
        o = existing.get((ev.id, h)) or outcome_cls(event_id=ev.id, horizon=h)
        if o.status in ("final", "unavailable"):
            continue
        o.computed_at = now
        if i is None:
            late = (today - local).days > 10
            o.status, o.note = ("unavailable", "aucun cours après l'événement") if late else ("pending", None)
        elif i + h >= len(b):
            late = (today - b[i][0]).days > 3 * h + 30
            o.status, o.note = ("unavailable", "cours manquants") if late else ("pending", None)
            o.ref_day, o.ref_close = b[i][0], b[i][1]
        else:
            win = b[i : i + h + 1]
            o.ref_day, o.ref_close, o.end_day, o.end_close = b[i][0], b[i][1], b[i + h][0], b[i + h][1]
            if broken([c for _, c, _ in win], [f for _, _, f in win]):
                o.status, o.note = "unavailable", "opération sur titre ou cours aberrant dans la fenêtre"
            else:
                o.ret = total_return(b[i][1], b[i][2], b[i + h][1], b[i + h][2])  # dividends included
                vm, sm = med.medians(ev.mic, sector, b[i][0], b[i + h][0], ev.isin) if ev.mic else (None, None)
                o.venue_median, o.sector_median = vm, sm
                o.excess_venue = o.ret - vm if vm is not None else None
                o.excess_sector = o.ret - sm if sm is not None else None
                o.status, o.note = "final", None if vm is not None else "pas assez de valeurs sur la place"
        rep.final += o.status == "final"
        rep.pending += o.status == "pending"
        rep.unavailable += o.status == "unavailable"
        s.add(o)


# --- scoreboard -----------------------------------------------------------------------------------------------------


def _vix_bucket(v: Any) -> str:
    if not isinstance(v, int | float):
        return "inconnu"
    return "VIX < 20" if v < 20 else "VIX ≥ 20"


@dataclass
class ScoreRow:
    source_key: str
    family: str
    kind: str
    direction: int | None
    stats: GroupStats
    by_regime: dict[str, GroupStats]
    by_vix: dict[str, GroupStats]
    by_macro: dict[str, GroupStats]
    sector: GroupStats


def scoreboard(s: Session, horizon: int = 5, min_n: int = 1, since: date | None = None) -> list[ScoreRow]:
    """Final measures grouped by (source, kind, direction): signed excess over the venue, by context."""
    from app.db.models import EventOutcome, MarketEvent

    q = (
        select(MarketEvent, EventOutcome)
        .join(EventOutcome, and_(EventOutcome.event_id == MarketEvent.id, EventOutcome.horizon == horizon))
        .where(EventOutcome.status == "final", EventOutcome.excess_venue.is_not(None))
    )
    if since is not None:
        q = q.where(MarketEvent.event_at >= datetime.combine(since, time.min, tzinfo=PARIS).astimezone(UTC))
    groups: dict[tuple[str, str, str, int | None], list[tuple[Any, Any]]] = defaultdict(list)
    for ev, o in s.execute(q):
        groups[(ev.source_key, ev.family, ev.kind, ev.direction)].append((ev, o))
    out = []
    for (src, fam, kind, d), items in groups.items():
        if len(items) < min_n:
            continue

        def split(key: Any, items: list[tuple[Any, Any]] = items, d: int | None = d) -> dict[str, GroupStats]:
            by: dict[str, list[float]] = defaultdict(list)
            for ev, o in items:
                by[key(ev.context or {})].append(signed(o.excess_venue, d))
            return {k: group_stats(v) for k, v in sorted(by.items())}

        out.append(ScoreRow(
            src, fam, kind, d, group_stats([signed(o.excess_venue, d) for _, o in items]),
            split(lambda c: str(c.get("regime") or "inconnu")), split(lambda c: _vix_bucket(c.get("vix"))),
            split(lambda c: "annonce macro proche" if c.get("annonce_macro") else "sans annonce macro"),
            group_stats([signed(o.excess_sector, d) for _, o in items if o.excess_sector is not None]),
        ))  # fmt: skip
    return sorted(out, key=lambda r: (-r.stats.n, r.source_key, r.kind))


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f} %".replace(".", ",")


def report_lines(rows: list[ScoreRow], horizon: int) -> list[str]:
    """The scoreboard as text (``events-scoreboard``)."""
    out = [f"Après chaque événement : écart à la médiane de la place à {horizon} séance(s), dans le sens de la source"]
    for r in rows:
        st = r.stats
        t = f"{st.t_stat:+.1f}" if st.t_stat is not None else "—"
        rate = f"{st.hit_rate:.0%}" if st.hit_rate is not None else "—"
        out.append(f"  {r.source_key:<28} {r.kind:<30} sens {r.direction if r.direction is not None else '·':>2}  "
                   f"n {st.n:>5}  moyenne {pct(st.mean):>9}  réussite {rate:>4}  t {t:>5}")  # fmt: skip
    if len(out) == 1:
        out.append("  aucune mesure finale pour l'instant (il faut que les séances suivant les événements existent)")
    return out


def outcome_for(s: Session, event_id: int, horizon: int) -> Any:
    from app.db.models import EventOutcome

    return s.scalar(select(EventOutcome).where(EventOutcome.event_id == event_id, EventOutcome.horizon == horizon))


def since_days(today: date, days: int) -> datetime:
    return datetime.combine(today - timedelta(days=days), time.min, tzinfo=PARIS).astimezone(UTC)
