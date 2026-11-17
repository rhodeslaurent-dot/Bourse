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

from app.domain.event_stats import (
    Contrast,
    GroupStats,
    broken,
    contrast,
    difference,
    group_stats,
    signed,
    total_return,
    venue_median,
)
from app.domain.timeutil import PARIS, from_store

HORIZONS = (1, 5, 20)
CONTROL_OFFSETS = (-60, 60)
SECTOR_MIN = 5
VENUE_MIN = 10


@dataclass(frozen=True)
class MeasureSettings:
    horizons: tuple[int, ...] = HORIZONS
    venue_min: int = VENUE_MIN  # values of the venue needed for its median
    sector_min: int = SECTOR_MIN  # values of the same sector on the venue needed for the sector median
    # 16/11/2026: « all » = every share of the venue with a close at both dates, whatever happened to it later; the
    # first production measure used today's universe — survivors by construction, which pulled every excess down
    population: str = "all"
    # 16/11/2026, control group fixed before any result was read: the same value, the same horizon, 60 sessions
    # before and 60 sessions after the event's reference close (docs/40 §40.3)
    control_offsets: tuple[int, ...] = CONTROL_OFFSETS


def settings(config: Any) -> MeasureSettings:
    """``event_journal.horizons``, ``.venue_min_members``, ``.sector_min_members`` (defaults when absent)."""
    g = (getattr(config, "raw", None) or {}).get("event_journal") or {}
    hz = tuple(sorted({int(h) for h in g.get("horizons", HORIZONS) if int(h) > 0})) or HORIZONS
    pop = str(g.get("median_population", "all"))
    offsets = tuple(int(x) for x in g.get("control_offsets", CONTROL_OFFSETS) if int(x) != 0) or CONTROL_OFFSETS
    return MeasureSettings(
        hz,
        int(g.get("venue_min_members", VENUE_MIN)),
        int(g.get("sector_min_members", SECTOR_MIN)),
        pop if pop in ("all", "universe") else "all",
        offsets,
    )


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
            .where(Instrument.mic == mic, PriceEod.day == d)
            .where(Instrument.in_universe.is_(True) if self.st.population == "universe" else Instrument.kind == "stock")
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


def _todo(s: Session, limit: int, n_horizons: int, offset: int = 0) -> list[Any]:
    """Events with a horizon not measured yet or still pending (for the event, or for its control at ``offset``),
    oldest first."""
    from sqlalchemy import func

    from app.db.models import EventControl, EventOutcome, MarketEvent

    model: Any = EventControl if offset else EventOutcome
    q = select(model.event_id).where(model.status != "pending")
    if offset:
        q = q.where(EventControl.offset == offset)
    done = q.group_by(model.event_id).having(func.count() >= n_horizons)
    return list(
        s.scalars(select(MarketEvent).where(MarketEvent.id.not_in(done)).order_by(MarketEvent.event_at).limit(limit))
    )


def reset(s: Session) -> int:
    """Forget every measure (16/11/2026: the method changed — returns now include dividends). The outcomes are
    derived from stored closes only, so the next ``measure`` rebuilds them identically from the same data."""
    from sqlalchemy import delete

    from app.db.models import EventControl, EventOutcome

    n = (s.execute(delete(EventOutcome)).rowcount or 0) + (s.execute(delete(EventControl)).rowcount or 0)
    s.flush()
    return int(n)


def measure(s: Session, today: date, calendars: Any | None = None, limit: int = 5000, now: datetime | None = None,
            st: MeasureSettings | None = None, offset: int = 0) -> MeasureReport:  # fmt: skip
    """Measure (or measure again) the ``limit`` oldest events whose horizons are not all final — or, with an
    ``offset`` in sessions, their control windows (the same value, ``offset`` sessions before or after).

    The batch is read **in time order** (16/11/2026, before the first run on the production base of 54 million
    closes): consecutive events share their sessions, so each venue's day of closes is read once and kept in a
    bounded cache; the closes of a value are read once per batch, over the batch's dates only. A control pass walks
    one offset at a time, so its days move in order too."""
    from app.db.models import EventControl, EventOutcome, Instrument

    now = now or datetime.now(UTC)
    st = st or MeasureSettings()
    cals = calendars if calendars is not None else _calendars()
    rep = MeasureReport()
    med = _Medians(s, st)
    events = _todo(s, limit, len(st.horizons), offset)
    if not events:
        return rep
    model: Any = EventControl if offset else EventOutcome
    existing = {}
    for k in range(0, len(events), 900):
        q = select(model).where(model.event_id.in_([e.id for e in events[k : k + 900]]))
        if offset:
            q = q.where(EventControl.offset == offset)
        existing.update({(o.event_id, o.horizon): o for o in s.scalars(q)})
    days = [from_store(e.event_at).date() for e in events]
    pad = timedelta(days=int(1.6 * abs(offset)) + 10) if offset else timedelta(0)  # sessions → calendar days
    lo = min(days) - timedelta(days=7) - (pad if offset < 0 else timedelta(0))
    hi = max(days) + timedelta(days=3 * max(st.horizons) + 40) + (pad if offset > 0 else timedelta(0))
    others = _same_source_days(s, events, lo, hi) if offset else {}
    bars: dict[str, list[tuple[date, float, float | None]]] = {}
    sectors: dict[str, str | None] = {}
    for ev in sorted(events, key=lambda e: (from_store(e.event_at), e.id)):
        if ev.isin not in bars:
            bars[ev.isin] = _bars(s, ev.isin, lo, hi)
            inst = s.get(Instrument, ev.isin)
            sectors[ev.isin] = inst.sector if inst is not None else None
        rep.events += 1
        _measure_one(ev, bars[ev.isin], sectors[ev.isin], today, now, cals, med, st, existing, rep, s, model,
                     offset, others.get((ev.isin, ev.source_key), ()))  # fmt: skip
        if rep.events % 2000 == 0:
            s.flush()
    s.flush()
    return rep


def _same_source_days(s: Session, events: list[Any], lo: date, hi: date) -> dict[tuple[str, str], tuple[date, ...]]:
    """(value, source) → the days of that source's events on the value: a control window holding one is not a
    window « without event »."""
    from app.db.models import MarketEvent

    isins = sorted({e.isin for e in events})
    start = datetime.combine(lo, time.min, tzinfo=PARIS).astimezone(UTC)
    end = datetime.combine(hi, time.max, tzinfo=PARIS).astimezone(UTC)
    out: dict[tuple[str, str], set[date]] = defaultdict(set)
    for k in range(0, len(isins), 900):
        for isin, src, at in s.execute(
            select(MarketEvent.isin, MarketEvent.source_key, MarketEvent.event_at).where(
                MarketEvent.isin.in_(isins[k : k + 900]), MarketEvent.event_at >= start, MarketEvent.event_at <= end
            )
        ):
            out[(isin, src)].add(from_store(at).astimezone(PARIS).date())
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _measure_one(ev: Any, b: list[tuple[date, float, float | None]], sector: str | None, today: date, now: datetime,
                 cals: Any, med: _Medians, st: MeasureSettings, existing: dict[tuple[int, int], Any],
                 rep: MeasureReport, s: Session, outcome_cls: Any, offset: int = 0,
                 others: tuple[date, ...] = ()) -> None:  # fmt: skip
    at = from_store(ev.event_at)
    venue = _venue(cals, ev.mic)
    local = at.astimezone(venue.tz if venue is not None else PARIS).date()
    r = next((k for k, (d, _, _) in enumerate(b) if d >= local), None)
    if r is not None and b[r][0] == local and _close_moment(venue, local) <= at:
        r = r + 1 if r + 1 < len(b) else None
    i = r + offset if r is not None else None  # the control window starts ``offset`` sessions away
    ahead = max(offset, 0)
    for h in st.horizons:
        o = existing.get((ev.id, h)) or (
            outcome_cls(event_id=ev.id, horizon=h, offset=offset) if offset else outcome_cls(event_id=ev.id, horizon=h)
        )
        if o.status in ("final", "unavailable"):
            continue
        o.computed_at = now
        if i is None or r is None:
            late = (today - local).days > 10 + ahead * 2
            o.status, o.note = ("unavailable", "aucun cours après l'événement") if late else ("pending", None)
        elif i < 0:
            o.status, o.note = "unavailable", "historique insuffisant avant l'événement"
        elif i + h >= len(b):
            late = (today - b[r][0]).days > 3 * (h + ahead) + 30
            o.status, o.note = ("unavailable", "cours manquants") if late else ("pending", None)
            if i < len(b):
                o.ref_day, o.ref_close = b[i][0], b[i][1]
        else:
            win = b[i : i + h + 1]
            o.ref_day, o.ref_close, o.end_day, o.end_close = b[i][0], b[i][1], b[i + h][0], b[i + h][1]
            first = b[i - 1][0] if i > 0 else b[i][0]  # an event published after the close before the window counts
            if others and any(first <= d <= b[i + h][0] for d in others):
                o.status, o.note = "unavailable", "autre événement de la même source dans la fenêtre"
            elif broken([c for _, c, _ in win], [f for _, _, f in win]):
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
    by_trend: dict[str, GroupStats] = field(default_factory=dict)  # index above / below its MM50 (17/11/2026)
    trend_vs_control: dict[str, Contrast] = field(default_factory=dict)  # gap to the control inside each bucket
    vix_vs_control: dict[str, Contrast] = field(default_factory=dict)
    control: GroupStats = GroupStats(0, None, None, None, None)  # the same values away from the source's events
    vs_control: Contrast = Contrast(0, 0, None, None)


def scoreboard(s: Session, horizon: int = 5, min_n: int = 1, since: date | None = None,
               control_offset: int | None = None, kinds: tuple[str, ...] | None = None,
               controls_by_context: bool = False) -> list[ScoreRow]:  # fmt: skip
    """Final measures grouped by (source, kind, direction): signed excess over the venue, by context. ``kinds``
    keeps some types only; ``controls_by_context`` adds the gap to the control inside each context bucket (index
    trend, VIX) — the controls of an event are put in its own event's bucket."""
    from app.db.models import EventOutcome, MarketEvent

    q = (
        select(MarketEvent, EventOutcome)
        .join(EventOutcome, and_(EventOutcome.event_id == MarketEvent.id, EventOutcome.horizon == horizon))
        .where(EventOutcome.status == "final", EventOutcome.excess_venue.is_not(None))
    )
    if kinds:
        q = q.where(MarketEvent.kind.in_(kinds))
    if since is not None:
        q = q.where(MarketEvent.event_at >= datetime.combine(since, time.min, tzinfo=PARIS).astimezone(UTC))
    groups: dict[tuple[str, str, str, int | None], list[tuple[Any, Any]]] = defaultdict(list)
    for ev, o in s.execute(q):
        groups[(ev.source_key, ev.family, ev.kind, ev.direction)].append((ev, o))
    controls = _control_values(s, horizon, since, control_offset, kinds, controls_by_context)
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
        trend_of = lambda c: str((c or {}).get("indice_mm50") or "inconnu")  # noqa: E731
        vix_of = lambda c: _vix_bucket((c or {}).get("vix"))  # noqa: E731
        out[-1].by_trend = split(trend_of)
        mine = controls.get((src, fam, kind, d), [])
        ctl = group_stats([signed(x, d) for x, _ in mine])
        out[-1].control, out[-1].vs_control = ctl, difference(out[-1].stats, ctl)
        if controls_by_context:
            for attr, key, part in (("trend_vs_control", trend_of, out[-1].by_trend),
                                    ("vix_vs_control", vix_of, out[-1].by_vix)):  # fmt: skip
                by: dict[str, list[float]] = defaultdict(list)
                for x, c in mine:
                    by[key(c)].append(signed(x, d))
                setattr(out[-1], attr, {k: difference(g, group_stats(by.get(k, []))) for k, g in part.items()})
    return sorted(out, key=lambda r: (-r.stats.n, r.source_key, r.kind))


def _control_values(
    s: Session,
    horizon: int,
    since: date | None,
    offset: int | None = None,
    kinds: tuple[str, ...] | None = None,
    with_context: bool = False,
) -> dict[tuple[str, str, str, int | None], list[tuple[float, dict[str, Any] | None]]]:
    """Excess over the venue of every final control window, by (source, family, kind, direction) of its event —
    with its event's context when asked (a control is read in the context of the event it stands for)."""
    from app.db.models import EventControl, MarketEvent

    ctx_col: Any = MarketEvent.context if with_context else None
    cols = [MarketEvent.source_key, MarketEvent.family, MarketEvent.kind, MarketEvent.direction,
            EventControl.excess_venue] + ([ctx_col] if with_context else [])  # fmt: skip
    q = (
        select(*cols)
        .join(EventControl, EventControl.event_id == MarketEvent.id)
        .where(EventControl.horizon == horizon, EventControl.status == "final", EventControl.excess_venue.is_not(None))
    )
    if kinds:
        q = q.where(MarketEvent.kind.in_(kinds))
    if since is not None:
        q = q.where(MarketEvent.event_at >= datetime.combine(since, time.min, tzinfo=PARIS).astimezone(UTC))
    if offset is not None:  # one control window only (17/11/2026: a run-up before the event sits in the −60 one)
        q = q.where(EventControl.offset == offset)
    out: dict[tuple[str, str, str, int | None], list[tuple[float, dict[str, Any] | None]]] = defaultdict(list)
    for row in s.execute(q):
        out[(row[0], row[1], row[2], row[3])].append((float(row[4]), row[5] if with_context else None))
    return out


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f} %".replace(".", ",")


def report_lines(rows: list[ScoreRow], horizon: int) -> list[str]:
    """The scoreboard as text (``events-scoreboard``)."""
    out = [f"Après chaque événement : écart à la médiane de la place à {horizon} séance(s), dans le sens de la source"]
    if any(r.control.n for r in rows):
        out.append("  (témoin : les mêmes valeurs 60 séances avant et après, hors événement de la source ; à lire : "
                   "l'écart au témoin, n ≥ 300 de chaque côté et t ≥ 3 en valeur absolue)")  # fmt: skip
    for r in rows:
        st = r.stats
        t = f"{st.t_stat:+.1f}" if st.t_stat is not None else "—"
        rate = f"{st.hit_rate:.0%}" if st.hit_rate is not None else "—"
        line = (f"  {r.source_key:<28} {r.kind:<30} sens {r.direction if r.direction is not None else '·':>2}  "
                f"n {st.n:>5}  moyenne {pct(st.mean):>9}  réussite {rate:>4}  t {t:>5}")  # fmt: skip
        if r.control.n:  # 16/11/2026: the same values away from the source's events, and the gap to them
            vc = r.vs_control
            tc = f"{vc.t_stat:+.1f}" if vc.t_stat is not None else "—"
            line += f"  | témoin n {r.control.n:>6} {pct(r.control.mean):>8}  écart {pct(vc.diff):>8}  t {tc:>5}"
        out.append(line)
    if len(out) == 1:
        out.append("  aucune mesure finale pour l'instant (il faut que les séances suivant les événements existent)")
        return out
    pairs = contrast_lines(rows)
    if pairs:
        out.append(
            "Contraste dans une même source : après « hausse » (achat, surprise positive) moins après « baisse »"
        )
        out.append("  (indemne de ce qui fait dériver toute la population de la source ; t ≥ 3 en valeur absolue)")
        out.extend(pairs)
    return out


def contrast_lines(rows: list[ScoreRow]) -> list[str]:
    """One line per source with both « up » and « down » events among the rows."""
    by_source: dict[str, tuple[list[GroupStats], list[GroupStats]]] = {}
    for r in rows:
        if r.direction in (1, -1):
            up, down = by_source.setdefault(r.source_key, ([], []))
            (up if r.direction == 1 else down).append(r.stats)
    out = []
    for source, (up, down) in by_source.items():
        if not up or not down:
            continue
        c = contrast(up, down)
        t = f"{c.t_stat:+.1f}" if c.t_stat is not None else "—"
        out.append(f"  {source:<28} n {c.n_up:>6} / {c.n_down:<6} écart {pct(c.diff):>9}  t {t:>5}")
    return out


def outcome_for(s: Session, event_id: int, horizon: int) -> Any:
    from app.db.models import EventOutcome

    return s.scalar(select(EventOutcome).where(EventOutcome.event_id == event_id, EventOutcome.horizon == horizon))


def since_days(today: date, days: int) -> datetime:
    return datetime.combine(today - timedelta(days=days), time.min, tzinfo=PARIS).astimezone(UTC)
