"""Availability mode (docs/02 §2.3): ``disponible`` / ``reunion`` / ``absent``.

The mode is an *input* of the engine. It is derived from a weekly default schedule
(``availability.schedule``, Réunion local time, or relative to the Paris open) and from
explicit overrides (``/mode reunion 2h``). Pure functions; the caller supplies the clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.config.schema import Mode, ScheduleSlot
from app.domain.timeutil import PARIS, REUNION, local_to_utc, parse_hhmm

MODES: tuple[Mode, ...] = ("disponible", "reunion", "absent")


@dataclass(frozen=True)
class ModeOverride:
    mode: Mode
    since: datetime
    until: datetime | None
    source: Literal["telegram", "web", "schedule", "api"]


@dataclass(frozen=True)
class ResolvedMode:
    mode: Mode
    source: str
    until: datetime | None = None
    slot: str | None = None


def _days_match(spec: str, weekday_iso: int) -> bool:
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            if int(a) <= weekday_iso <= int(b):
                return True
        elif part and int(part) == weekday_iso:
            return True
    return False


def slot_window_utc(slot: ScheduleSlot, day: date, market_open_hhmm: str = "09:00") -> tuple[datetime, datetime] | None:
    """UTC window of a schedule slot on ``day`` (Réunion date). ``None`` if unconfirmed/incomplete."""
    if not slot.confirmed:
        return None
    if slot.ref == "market_open":
        if not slot.offset_min or len(slot.offset_min) != 2:
            return None
        open_utc = local_to_utc(day, market_open_hhmm, PARIS)
        return open_utc + timedelta(minutes=slot.offset_min[0]), open_utc + timedelta(minutes=slot.offset_min[1])
    if slot.from_ is None or slot.to is None:
        return None
    start = local_to_utc(day, slot.from_, REUNION)
    end = local_to_utc(day, slot.to, REUNION)
    return start, end


def mode_from_schedule(now_utc: datetime, schedule: list[ScheduleSlot], default_mode: Mode) -> ResolvedMode:
    local = now_utc.astimezone(REUNION)
    day = local.date()
    weekday = local.isoweekday()
    for i, slot in enumerate(schedule):
        if not _days_match(slot.days, weekday):
            continue
        win = slot_window_utc(slot, day)
        if win is None:
            continue
        start, end = win
        if start <= now_utc < end:
            return ResolvedMode(slot.mode, "schedule", end, f"slot#{i}")
    return ResolvedMode(default_mode, "schedule_default")


def resolve_mode(
    now_utc: datetime,
    schedule: list[ScheduleSlot],
    default_mode: Mode,
    override: ModeOverride | None = None,
) -> ResolvedMode:
    """Explicit override wins while active; then the weekly schedule; then the default."""
    if override is not None and override.since <= now_utc and (override.until is None or now_utc < override.until):
        return ResolvedMode(override.mode, override.source, override.until)
    return mode_from_schedule(now_utc, schedule, default_mode)


def unconfirmed_slots(schedule: list[ScheduleSlot]) -> list[ScheduleSlot]:
    return [s for s in schedule if not s.confirmed]


_DURATION_RE = re.compile(r"^(\d+)\s*(min|m|h|j|d)$", re.I)


def parse_duration(text: str) -> timedelta | None:
    """``2h`` / ``30m`` / ``30min`` / ``1j`` / ``1d``. Returns ``None`` if not parseable."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit in ("m", "min"):
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(days=n)


@dataclass(frozen=True)
class ModeCommand:
    mode: Mode
    duration: timedelta | None


def parse_mode_command(text: str) -> ModeCommand | None:
    """Parse ``/mode disponible|reunion|absent [durée]``. ``None`` if invalid."""
    parts = text.strip().split()
    if not parts:
        return None
    if parts[0].startswith("/mode"):
        parts = parts[1:]
    if not parts:
        return None
    raw_mode = parts[0].lower().replace("é", "e")
    mode = next((m for m in MODES if m == raw_mode), None)
    if mode is None:
        return None
    duration = None
    if len(parts) > 1:
        duration = parse_duration(parts[1])
        if duration is None:
            return None
    return ModeCommand(mode, duration)


def end_of_local_day(now_utc: datetime) -> datetime:
    local = now_utc.astimezone(REUNION)
    return datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=REUNION).astimezone(now_utc.tzinfo)


__all__ = [
    "MODES",
    "ModeCommand",
    "ModeOverride",
    "ResolvedMode",
    "end_of_local_day",
    "mode_from_schedule",
    "parse_duration",
    "parse_hhmm",
    "parse_mode_command",
    "resolve_mode",
    "slot_window_utc",
    "unconfirmed_slots",
]
