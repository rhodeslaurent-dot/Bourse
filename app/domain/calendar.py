"""Market calendars *per venue* (MIC) and SRD calendar. Pure domain, no I/O.

CLAUDE.md rule 4 and docs/02 §2.4: a job processing an instrument uses the calendar of that
instrument's venue, never Euronext Paris by default. Data comes from ``config/market_calendars.yaml``
(loaded by the caller) or from the ``market_calendar`` table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.timeutil import UTC, parse_hhmm


class DayStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    HALF_DAY = "half_day"


@dataclass(frozen=True)
class SessionInfo:
    mic: str
    day: date
    status: DayStatus
    open_time: time | None
    close_time: time | None
    tz: ZoneInfo

    @property
    def is_open(self) -> bool:
        return self.status != DayStatus.CLOSED

    def open_utc(self) -> datetime | None:
        if self.open_time is None:
            return None
        return datetime.combine(self.day, self.open_time, tzinfo=self.tz).astimezone(UTC)

    def close_utc(self) -> datetime | None:
        if self.close_time is None:
            return None
        return datetime.combine(self.day, self.close_time, tzinfo=self.tz).astimezone(UTC)


@dataclass
class VenueCalendar:
    mic: str
    name: str
    tz: ZoneInfo
    open_time: time
    close_time: time
    holidays: set[date] = field(default_factory=set)
    half_days: dict[date, time] = field(default_factory=dict)
    source: str = ""
    known_years: set[int] = field(default_factory=set)

    def session(self, d: date) -> SessionInfo:
        if d.year not in self.known_years:
            raise ValueError(f"calendrier {self.mic} inconnu pour l'année {d.year}: refus de deviner")
        if d.weekday() >= 5 or d in self.holidays:
            return SessionInfo(self.mic, d, DayStatus.CLOSED, None, None, self.tz)
        if d in self.half_days:
            return SessionInfo(self.mic, d, DayStatus.HALF_DAY, self.open_time, self.half_days[d], self.tz)
        return SessionInfo(self.mic, d, DayStatus.OPEN, self.open_time, self.close_time, self.tz)

    def is_open(self, d: date) -> bool:
        return self.session(d).is_open

    def is_half_day(self, d: date) -> bool:
        return self.session(d).status == DayStatus.HALF_DAY

    def is_open_at(self, dt: datetime) -> bool:
        """True if ``dt`` (aware) falls inside the continuous session of that local day."""
        local = dt.astimezone(self.tz)
        s = self.session(local.date())
        if not s.is_open or s.open_time is None or s.close_time is None:
            return False
        return s.open_time <= local.time().replace(tzinfo=None) <= s.close_time

    def previous_session(self, d: date) -> date:
        cur = d
        for _ in range(0, 15):
            cur = date.fromordinal(cur.toordinal() - 1)
            if self.is_open(cur):
                return cur
        raise ValueError("no session found in the previous 15 days")

    def next_session(self, d: date) -> date:
        cur = d
        for _ in range(0, 15):
            cur = date.fromordinal(cur.toordinal() + 1)
            if self.is_open(cur):
                return cur
        raise ValueError("no session found in the next 15 days")

    def sessions_between(self, start: date, end: date) -> list[date]:
        out = []
        cur = start
        while cur <= end:
            if self.is_open(cur):
                out.append(cur)
            cur = date.fromordinal(cur.toordinal() + 1)
        return out


@dataclass
class MarketCalendars:
    venues: dict[str, VenueCalendar]
    eodhd_to_mic: dict[str, str] = field(default_factory=dict)
    p0_mics: list[str] = field(default_factory=list)
    version: str = ""

    def get(self, mic: str) -> VenueCalendar:
        if mic not in self.venues:
            raise KeyError(f"calendrier inconnu pour la place {mic}")
        return self.venues[mic]

    def mic_for_eodhd(self, code: str) -> str:
        return self.eodhd_to_mic[code]

    def any_p0_open(self, d: date) -> bool:
        return any(self.get(m).is_open(d) for m in self.p0_mics)

    def all_open(self, d: date, mics: list[str]) -> bool:
        return all(self.get(m).is_open(d) for m in mics)

    def open_mics(self, d: date, mics: list[str] | None = None) -> list[str]:
        return [m for m in (mics or list(self.venues)) if self.get(m).is_open(d)]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketCalendars:
        venues: dict[str, VenueCalendar] = {}
        for mic, v in data["markets"].items():
            holidays: set[date] = set()
            half: dict[date, time] = {}
            years: set[int] = set()
            for year, lst in (v.get("holidays") or {}).items():
                years.add(int(year))
                holidays.update(date.fromisoformat(str(x)) for x in lst)
            for year, mp in (v.get("half_days") or {}).items():
                years.add(int(year))
                for d, close in mp.items():
                    half[date.fromisoformat(str(d))] = parse_hhmm(close)
            venues[mic] = VenueCalendar(
                mic=mic,
                name=v.get("name", mic),
                tz=ZoneInfo(v.get("tz", "Europe/Paris")),
                open_time=parse_hhmm(v.get("open", "09:00")),
                close_time=parse_hhmm(v.get("close", "17:30")),
                holidays=holidays,
                half_days=half,
                source=v.get("source", ""),
                known_years=years,
            )
        return cls(
            venues=venues,
            eodhd_to_mic=dict(data.get("eodhd_to_mic") or {}),
            p0_mics=list(data.get("p0_mics") or []),
            version=str(data.get("version", "")),
        )


@dataclass(frozen=True)
class SrdCycle:
    liquidation_date: date
    settlement_date: date | None
    confirmed: bool


@dataclass
class SrdCalendar:
    cycles: list[SrdCycle]

    @classmethod
    def from_params(cls, cal_2026: list[list[date]], cal_2027: list[date] | None = None) -> SrdCalendar:
        cycles = [SrdCycle(a, b, True) for a, b in cal_2026]
        for d in cal_2027 or []:
            cycles.append(SrdCycle(d, None, False))
        cycles.sort(key=lambda c: c.liquidation_date)
        return cls(cycles)

    def next_liquidation(self, d: date) -> SrdCycle:
        for c in self.cycles:
            if c.liquidation_date >= d:
                return c
        raise ValueError(f"aucune liquidation SRD connue après {d}")

    def sessions_until_liquidation(self, d: date, venue: VenueCalendar) -> int:
        nxt = self.next_liquidation(d)
        return len(venue.sessions_between(d, nxt.liquidation_date)) - (1 if venue.is_open(d) else 0)


def load_market_calendars(path: str = "config/market_calendars.yaml") -> MarketCalendars:
    """Thin I/O helper (kept out of the pure functions above)."""
    import yaml

    with open(path, encoding="utf-8") as fh:
        return MarketCalendars.from_dict(yaml.safe_load(fh))
