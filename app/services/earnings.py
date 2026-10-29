# ruff: noqa: E501
"""docs/34 §34.4 — history of earnings announcements: load (EODHD calendar, else Yahoo) and coverage.

The study of the missed rises and registration n° 3 need to know, for each past session, whether the value
published its results that day (the literature's best documented short-horizon drift starts there). Coverage is
printed per year and venue group before anything is decided: a period without announcements cannot test them.
"""

from __future__ import annotations

import logging
import time as _time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.base import ProviderError, ProviderUnavailable, QuotaExceeded
from app.data.providers.earnings import EarningsRecord, eodhd_earnings, yahoo_earnings
from app.db.models import EarningsEvent, Instrument
from app.domain.calendar import MarketCalendars

log = logging.getLogger("bourse.earnings")
Progress = Callable[[str], None]


@dataclass
class LoadReport:
    values: int = 0
    with_events: int = 0
    added: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def store(s: Session, isin: str, recs: list[EarningsRecord], now: datetime | None = None) -> int:
    """Insert the records not yet known for (isin, day, source); an existing row is completed, never erased."""
    now = now or datetime.now(UTC)
    known = {(e.day, e.source): e for e in s.scalars(select(EarningsEvent).where(EarningsEvent.isin == isin))}
    added = 0
    for r in recs:
        row = known.get((r.day, r.source))
        if row is None:
            row = EarningsEvent(isin=isin, day=r.day, ts=r.ts, when=r.when, eps_actual=r.eps_actual,
                                eps_estimate=r.eps_estimate, surprise_pct=r.surprise_pct, source=r.source,
                                received_at=now)  # fmt: skip
            s.add(row)
            known[(r.day, r.source)] = row
            added += 1
            continue
        for k in ("ts", "eps_actual", "eps_estimate", "surprise_pct"):
            if getattr(row, k) is None and getattr(r, k) is not None:
                setattr(row, k, getattr(r, k))
        if row.when == "unknown" and r.when != "unknown":
            row.when = r.when
    return added


def _venue(calendars: MarketCalendars | None, mic: str | None) -> Any:
    try:
        return calendars.get(mic) if calendars is not None and mic else None
    except KeyError:
        return None


def load_history(
    s: Session,
    config: LoadedConfig,
    calendars: MarketCalendars | None,
    start: date,
    end: date,
    isins: list[str] | None = None,
    source: str = "auto",
    yahoo_pages: int = 3,
    yahoo_interval_s: float = 1.0,
    eodhd: Any | None = None,
    yahoo_fetch: Callable[[str, int, int], Any] | None = None,
    progress: Progress | None = None,
    max_values: int | None = None,
) -> LoadReport:
    """Announcements of the stocks (in the universe, or ``isins``) between ``start`` and ``end``."""
    from app.data.providers.yahoo_fundamentals import yahoo_symbol

    say = progress or (lambda m: None)
    rep = LoadReport()
    q = (
        select(Instrument).where(Instrument.isin.in_(isins))
        if isins
        else select(Instrument).where(Instrument.in_universe.is_(True))
    )
    insts = [i for i in s.scalars(q.order_by(Instrument.isin)) if (i.kind or "stock") == "stock" and i.ticker_eodhd]
    if max_values:
        insts = insts[:max_values]
    rep.values = len(insts)
    use_eodhd = source in ("auto", "eodhd")
    if use_eodhd:
        if eodhd is None:
            from app.data.providers.eodhd import eodhd_from

            eodhd = eodhd_from(config)
        by_symbol = {i.ticker_eodhd: i for i in insts}
        symbols = sorted(by_symbol)
        try:
            for k in range(0, len(symbols), 50):
                chunk = symbols[k : k + 50]
                for r in eodhd_earnings(eodhd._get, chunk, start, end):
                    inst = by_symbol.get(r.symbol)
                    if inst is not None:
                        rep.added += store(s, inst.isin, [r])
                        rep.source_counts["eodhd"] = rep.source_counts.get("eodhd", 0) + 1
                say(f"EODHD : {min(k + 50, len(symbols))}/{len(symbols)} symboles")
        except ProviderUnavailable as exc:
            rep.notes.append(f"EODHD indisponible ({exc}) : Yahoo utilisé")
            say(rep.notes[-1])
            use_eodhd = False
        except QuotaExceeded as exc:
            rep.notes.append(f"quota EODHD atteint ({exc}) : arrêt")
            return rep
    if source == "yahoo" or (source == "auto" and not use_eodhd):
        for n, inst in enumerate(insts, 1):
            sym = yahoo_symbol(inst.ticker_eodhd)
            if sym is None:
                rep.unavailable.append(inst.isin)
                continue
            venue = _venue(calendars, inst.mic)
            hours = (
                (
                    venue.open_time.hour + venue.open_time.minute / 60,
                    venue.close_time.hour + venue.close_time.minute / 60,
                )
                if venue is not None
                else (9.0, 17.5)
            )
            try:
                recs = yahoo_earnings(sym, yahoo_pages, yahoo_fetch, venue.tz if venue is not None else None, hours)
            except (ProviderUnavailable, ProviderError) as exc:
                rep.unavailable.append(inst.isin)
                log.info("résultats %s indisponibles : %s", inst.isin, exc)
                recs = []
            recs = [r for r in recs if start <= r.day <= end]
            rep.added += store(s, inst.isin, recs)
            rep.source_counts["yahoo"] = rep.source_counts.get("yahoo", 0) + len(recs)
            if n % 50 == 0:
                s.flush()
                say(f"Yahoo : {n}/{len(insts)} valeurs, {rep.added} publication(s) ajoutée(s)")
            if yahoo_interval_s and yahoo_fetch is None:
                _time.sleep(yahoo_interval_s)  # politeness towards an unofficial source
    s.flush()
    rep.with_events = len({e.isin for e in s.scalars(select(EarningsEvent)) if e.isin in {i.isin for i in insts}})
    return rep


def coverage(s: Session, config: LoadedConfig, calendars: MarketCalendars, start: date, end: date) -> dict[str, Any]:
    """Per year and venue group: values with at least one announcement, announcements, share with an EPS surprise.
    No price is read."""
    from app.services.research import mic_groups

    groups = mic_groups(config, calendars)
    mic_of = {i.isin: i.mic for i in s.scalars(select(Instrument))}
    out: dict[str, dict[int, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"valeurs": 0, "publications": 0, "avec_surprise": 0})
    )
    seen: dict[tuple[str, int], set[str]] = defaultdict(set)
    for e in s.scalars(select(EarningsEvent).where(EarningsEvent.day >= start, EarningsEvent.day <= end)):
        g = groups.get(mic_of.get(e.isin) or "", "hors groupes")
        cell = out[g][e.day.year]
        cell["publications"] += 1
        cell["avec_surprise"] += int(
            e.surprise_pct is not None or (e.eps_actual is not None and e.eps_estimate is not None)
        )
        seen[(g, e.day.year)].add(e.isin)
    for (g, y), isins in seen.items():
        out[g][y]["valeurs"] = len(isins)
    return {g: dict(sorted(v.items())) for g, v in sorted(out.items())}


def coverage_text(cov: dict[str, Any]) -> str:
    lines = ["Couverture des publications de résultats (valeurs · publications · avec BPA publié et estimé)"]
    for g, years in cov.items():
        lines.append(f"  {g}")
        for y, c in years.items():
            lines.append(
                f"    {y} : {c['valeurs']:>5} valeurs · {c['publications']:>6} publications · {c['avec_surprise']:>6} avec surprise"
            )
    return "\n".join(lines)


def fill_next_dates(s: Session, today: date) -> int:
    """``instruments.earnings_next_date`` from the next stored announcement when the screener gave none (or a past
    one); a date the screener gave and still to come is kept. Returns the number of instruments filled."""
    nxt: dict[str, tuple[date, str]] = {}
    for e in s.scalars(select(EarningsEvent).where(EarningsEvent.day >= today).order_by(EarningsEvent.day)):
        nxt.setdefault(e.isin, (e.day, e.source))
    n = 0
    for inst in s.scalars(select(Instrument).where(Instrument.isin.in_(list(nxt)))) if nxt else []:
        if inst.earnings_next_date is not None and inst.earnings_next_date >= today:
            continue
        inst.earnings_next_date, inst.earnings_source = nxt[inst.isin][0], nxt[inst.isin][1]
        n += 1
    s.flush()
    return n
