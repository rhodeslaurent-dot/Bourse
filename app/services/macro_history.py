"""``macro-history-load`` (docs/22 §22.7, docs/07 §7.9.5): past macro announcements into ``macro_events``.

R14 measures the exposure of a value to an announcement on its opening gaps at the last ``events.history_count``
announcements of the same family; with fewer than ``events.min_history`` the exposure is unknown and the entry stays
frozen. The weekly ForexFactory feed cannot build that history before months: this loader fills it from EODHD (if
the plan includes it) and from the official calendars, one row per family and date (dedup across sources, the
first stored wins), each row with its source and reception time. Only past dates are stored (strictly before the
Paris day of the load): the coming announcements stay those of the weekly feed, with their time.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.base import ProviderError, ProviderUnavailable, QuotaExceeded
from app.data.providers.macro_history import PARSERS, PastEvent, eodhd_events, fetch_text, official_urls
from app.db.models import MacroEvent
from app.domain.timeutil import PARIS

log = logging.getLogger("bourse.macro_history")


@dataclass
class HistoryReport:
    added: dict[str, int] = field(default_factory=dict)  # per source
    already_known: int = 0
    unavailable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _families(config: LoadedConfig) -> dict[str, tuple[str, list[str]]]:
    from app.services.engine import _macro_families

    return _macro_families(config)


def _paris_day(ts: datetime) -> date:
    return (ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts).astimezone(PARIS).date()


def store(s: Session, config: LoadedConfig, events: list[PastEvent], now: datetime | None = None) -> tuple[int, int]:
    """Insert the past events not yet known for (family, Paris date); returns (added, already known)."""
    now = now or datetime.now(UTC)
    today = now.astimezone(PARIS).date()
    fams = _families(config)
    known: dict[str, set[date]] = {}
    added = skipped = 0
    for e in events:
        if e.family not in fams or e.day >= today:
            continue
        ccy, titles = fams[e.family]
        if e.family not in known:
            rows = s.scalars(select(MacroEvent).where(MacroEvent.currency == ccy, MacroEvent.title.in_(titles)))
            known[e.family] = {_paris_day(r.ts) for r in rows}
        day = _paris_day(e.ts) if e.ts is not None else e.day
        if day in known[e.family]:
            skipped += 1
            continue
        # date only: 00:00 Paris of the official date (no time is invented; the measure reads the date)
        ts = e.ts if e.ts is not None else datetime.combine(e.day, datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
        s.add(MacroEvent(ts=ts, currency=ccy, title=titles[0], impact="High", source=e.source[:32], received_at=now))
        known[e.family].add(day)
        added += 1
    s.flush()
    return added, skipped


def load(
    s: Session,
    config: LoadedConfig,
    start: date,
    end: date,
    source: str = "auto",
    eodhd: Any | None = None,
    fetch: Callable[[str], str | None] | None = None,
    progress: Callable[[str], None] | None = None,
    pause_s: float = 1.0,
    now: datetime | None = None,
) -> HistoryReport:
    """(a) EODHD economic events, then (b) the official pages (ECB, Fed, BLS); ``source`` ∈ auto | eodhd | officiel."""
    say = progress or (lambda m: None)
    rep = HistoryReport()

    def keep(evs: list[PastEvent]) -> None:
        by_src: dict[str, list[PastEvent]] = {}
        for e in evs:
            if start <= e.day <= end:
                by_src.setdefault(e.source, []).append(e)
        for src, lst in by_src.items():
            a, k = store(s, config, lst, now)
            rep.added[src] = rep.added.get(src, 0) + a
            rep.already_known += k

    if source in ("auto", "eodhd"):
        try:
            if eodhd is None:
                from app.data.providers.eodhd import eodhd_from

                eodhd = eodhd_from(config)
            keep(eodhd_events(eodhd._get, start, end))
            say(f"EODHD : {rep.added.get('eodhd_economic_events', 0)} annonce(s) ajoutée(s)")
        except (ProviderUnavailable, QuotaExceeded, ProviderError) as exc:
            rep.unavailable.append(f"EODHD : {exc}")
            say(rep.unavailable[-1])
    if source in ("auto", "officiel"):
        get = fetch or fetch_text
        for name, parser in PARSERS.items():
            for n, url in enumerate(official_urls(name, start, end)):
                try:
                    text = get(url)
                except ProviderUnavailable as exc:
                    rep.unavailable.append(f"{name} : {exc}")
                    say(rep.unavailable[-1])
                    if n == 0:
                        break  # the main page refused: the others of the same host would be too
                    continue
                if text:
                    keep(parser(text))
                if pause_s and fetch is None:
                    _time.sleep(pause_s)  # politeness towards official sites
            say(f"{name} : lu")
    return rep


def coverage(s: Session, config: LoadedConfig, until: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Per family: past dates stored (distinct Paris days), first and last, rows per source, and whether the R14
    exposure can be measured (≥ ``events.min_history``)."""
    until = until or datetime.now(UTC)
    min_h = int((getattr(config.params, "events", None) or {}).get("min_history", 4))
    out: dict[str, dict[str, Any]] = {}
    for fam, (ccy, titles) in _families(config).items():
        rows = list(
            s.scalars(
                select(MacroEvent).where(
                    MacroEvent.currency == ccy, MacroEvent.title.in_(titles), MacroEvent.ts < until
                )
            )
        )
        days = sorted({_paris_day(r.ts) for r in rows})
        src: dict[str, int] = {}
        for r in rows:
            src[r.source] = src.get(r.source, 0) + 1
        out[fam] = {
            "devise": ccy,
            "dates": len(days),
            "premiere": days[0].isoformat() if days else None,
            "derniere": days[-1].isoformat() if days else None,
            "sources": src,
            "mesurable": len(days) >= min_h,
        }
    return out


def coverage_text(cov: dict[str, dict[str, Any]], min_history: int) -> str:
    lines = [f"Historique des annonces macro (exposition R14 mesurable à partir de {min_history} dates)"]
    for fam, c in cov.items():
        state = "mesurable" if c["mesurable"] else f"en cours de constitution ({c['dates']}/{min_history})"
        span = f"{c['premiere']} → {c['derniere']}" if c["dates"] else "aucune date"
        lines.append(f"  {fam:<13} {c['devise']} : {c['dates']:>4} dates ({span}) · {state} · sources {c['sources']}")
    return "\n".join(lines)
