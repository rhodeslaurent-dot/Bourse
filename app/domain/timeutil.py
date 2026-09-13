"""Timezone helpers (CLAUDE.md rule 4): storage UTC, scheduling Europe/Paris, display Indian/Reunion.

Pure functions only. Réunion has no DST (UTC+4), Paris alternates UTC+1/UTC+2, so the Paris
open (09:00) is 11:00 Réunion in summer and 12:00 in winter.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
PARIS = ZoneInfo("Europe/Paris")
REUNION = ZoneInfo("Indian/Reunion")


def ensure_utc(dt: datetime) -> datetime:
    """Return an aware UTC datetime; naive input is *rejected* (never guess a timezone)."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime refused: all stored timestamps must be timezone-aware")
    return dt.astimezone(UTC)


def from_store(dt: datetime) -> datetime:
    """Datetime read back from the database: storage is UTC (rule 4), so a naive value (SQLite
    drops the offset) is *known* to be UTC. Never use this on external data."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def to_paris(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(PARIS)


def to_reunion(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(REUNION)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def local_to_utc(d: date, hhmm: str | time, tz: ZoneInfo) -> datetime:
    t = parse_hhmm(hhmm) if isinstance(hhmm, str) else hhmm
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC)


def paris_to_reunion_time(d: date, hhmm: str) -> time:
    """Wall-clock time in Réunion corresponding to ``hhmm`` Paris time on day ``d``."""
    return local_to_utc(d, hhmm, PARIS).astimezone(REUNION).time().replace(tzinfo=None)


def paris_utc_offset_hours(d: date) -> int:
    """+2 in summer (CEST), +1 in winter (CET)."""
    off = datetime.combine(d, time(12, 0), tzinfo=PARIS).utcoffset() or timedelta(0)
    return int(off.total_seconds() // 3600)


def is_paris_summer_time(d: date) -> bool:
    return paris_utc_offset_hours(d) == 2


def market_open_reunion(d: date, open_hhmm: str = "09:00") -> time:
    """Réunion wall-clock time of the Paris open on ``d`` (11:00 summer / 12:00 winter)."""
    return paris_to_reunion_time(d, open_hhmm)


def format_dual(dt: datetime) -> str:
    """Display helper: Réunion time first, Paris in parentheses (docs/09 §9.5)."""
    r = to_reunion(dt)
    p = to_paris(dt)
    return f"{r:%H:%M} Réunion ({p:%H:%M} Paris)"
