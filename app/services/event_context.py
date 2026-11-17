"""Step 1 (17/11/2026) — the context of past events, completed point in time.

The journal (lot S2) stored with each event the regime of its venue, the VIX and a major macro announcement as known
on its day. The regime exists only since 20/10/2026 and the VIX series since 2023: most events since 2016 had an
« unknown » context. Two facts are completed here from stored series, as known **before** the event's day:

- ``vix``: the VIX close of the last session before the day (within 7 days), when missing;
- ``indice_mm50``: the venue's index above or below the mean of its last 50 closes (« au-dessus » / « en dessous »),
  with ``indice_de`` (the venue, or ``EU`` when the venue has no index) — a trend read from the index itself, with no
  universe and so no survivor.

A value already stored is never replaced (it was the one known at the time). Facts only: no value is estimated.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_stats import trend_flags
from app.domain.timeutil import PARIS, from_store

VIX_MAX_AGE_DAYS = 7


@dataclass
class Series:
    """Sorted days and their values (a VIX close, or the index's trend flag)."""

    days: list[date] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)

    def before(self, day: date, max_age_days: int | None = None) -> Any:
        k = bisect_left(self.days, day) - 1  # strictly before ``day``
        if k < 0:
            return None
        if max_age_days is not None and (day - self.days[k]).days > max_age_days:
            return None
        return self.values[k]


@dataclass
class ContextSeries:
    vix: Series
    trends: dict[str, Series]  # market (MIC or EU) → trend flags of its index


def load_series(s: Session, config: Any) -> ContextSeries:
    from app.db.models import MacroPoint, PriceEod
    from app.services.indices import index_specs

    vix = Series()
    for d, v in s.execute(
        select(MacroPoint.day, MacroPoint.value).where(MacroPoint.indicator == "vix").order_by(MacroPoint.day)
    ):
        vix.days.append(d)
        vix.values.append(float(v))
    trends: dict[str, Series] = {}
    for spec in index_specs(config if getattr(config, "params", None) is not None else None):
        rows = s.execute(select(PriceEod.day, PriceEod.close).where(PriceEod.isin == spec.id).order_by(PriceEod.day))
        days, closes = [], []
        for d, c in rows:
            days.append(d)
            closes.append(float(c))
        trends[spec.market] = Series(days, trend_flags(closes))
    return ContextSeries(vix, trends)


def trend_label(flag: bool | None) -> str | None:
    return None if flag is None else ("au-dessus" if flag else "en dessous")


def completed(ctx: dict[str, Any] | None, mic: str | None, day: date, series: ContextSeries) -> dict[str, Any] | None:
    """The context with its missing facts added (None when nothing changes)."""
    out = dict(ctx or {})
    changed = False
    if out.get("vix") is None:
        v = series.vix.before(day, VIX_MAX_AGE_DAYS)
        if v is not None:
            out["vix"], changed = round(float(v), 2), True
    if out.get("indice_mm50") is None:
        market = mic if mic in series.trends and series.trends[mic].days else "EU"
        label = trend_label(series.trends[market].before(day)) if market in series.trends else None
        if label is not None:
            out["indice_mm50"], out["indice_de"], changed = label, market, True
    return out if changed else None


@dataclass
class ContextReport:
    events: int = 0
    completed: int = 0
    last_id: int = 0

    def line(self) -> str:
        return f"{self.events} événement(s) relu(s), {self.completed} contexte(s) complété(s)"


def complete_batch(
    s: Session, series: ContextSeries, since: date, after_id: int = 0, limit: int = 5000
) -> ContextReport:
    """One batch of events (by id) published since ``since``: missing VIX and index trend added, point in time."""
    from datetime import UTC, datetime, time

    from app.db.models import MarketEvent

    start = datetime.combine(since, time.min, tzinfo=PARIS).astimezone(UTC)
    rep = ContextReport(last_id=after_id)
    evs = list(
        s.scalars(
            select(MarketEvent)
            .where(MarketEvent.id > after_id, MarketEvent.event_at >= start)
            .order_by(MarketEvent.id)
            .limit(limit)
        )
    )
    for ev in evs:
        rep.events += 1
        rep.last_id = ev.id
        day = from_store(ev.event_at).astimezone(PARIS).date()
        new = completed(ev.context, ev.mic, day, series)
        if new is not None:
            ev.context = new
            rep.completed += 1
    s.flush()
    return rep
