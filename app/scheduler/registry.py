"""Build APScheduler triggers from ``params.yaml › jobs`` (Europe/Paris unless ``tz``)."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config.schema import JobSpec, Params
from app.domain.timeutil import PARIS

DEFAULT_MISFIRE_MIN = 10

# Phase 0 ships only the jobs below; the others are declared in params.yaml and appear on
# /sante as "non implémenté" until their phase.
IMPLEMENTED_JOBS: dict[str, str] = {
    "test_job": "app.scheduler.jobs.test_job:run",
    "backup": "app.scheduler.jobs.backup:run",
    "portfolio_sync": "app.scheduler.jobs.portfolio_sync:run",
    "portfolio_sync_intraday": "app.scheduler.jobs.portfolio_sync:run",
    "position_monitor": "app.scheduler.jobs.position_monitor:run",
    "universe_refresh": "app.scheduler.jobs.universe_refresh:run",
    "eod_backfill_check": "app.scheduler.jobs.eod_backfill_check:run",
    "daily_regime": "app.scheduler.jobs.daily_regime:run",
}


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    variant: str
    trigger: BaseTrigger
    misfire_grace_seconds: int
    spec: JobSpec


def _cron(expr: str, tz: ZoneInfo) -> CronTrigger:
    return CronTrigger.from_crontab(expr, timezone=tz)


def _window_interval(spec: JobSpec, tz: ZoneInfo) -> BaseTrigger:
    """``every_minutes``/``every_seconds`` inside ``window_local`` on ``days`` → cron with ranges."""
    days = spec.days or "1-5"
    start, end = spec.window_local or ["09:00", "17:40"]
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    if spec.every_seconds and spec.every_seconds < 60:
        return IntervalTrigger(seconds=spec.every_seconds, timezone=tz)
    minutes = spec.every_minutes or (spec.every_seconds or 60) // 60 or 1
    # cron day_of_week uses 0=mon; params use ISO 1=mon → convert ranges like "1-5" → "mon-fri"
    dow = _iso_days_to_cron(days)
    hour = f"{sh}-{eh}"
    return CronTrigger(minute=f"*/{minutes}" if minutes > 1 else "*", hour=hour, day_of_week=dow, timezone=tz)


def _iso_days_to_cron(days: str) -> str:
    names = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}
    out = []
    for part in days.split(","):
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            out.append(f"{names[a]}-{names[b]}")
        else:
            out.append(names[int(part)])
    return ",".join(out)


def _resolve_path(params: Params, dotted: str) -> object:
    cur: object = params
    for part in dotted.split("."):
        cur = getattr(cur, part, None) if not isinstance(cur, dict) else cur.get(part)
        if cur is None:
            return None
    return cur


def build_schedule(params: Params, only_implemented: bool = True) -> list[ScheduledJob]:
    jobs: list[ScheduledJob] = []
    specs = params.jobs.specs()
    for name, spec in specs.items():
        if only_implemented and name not in IMPLEMENTED_JOBS:
            continue
        if not spec.enabled:
            continue
        tz = ZoneInfo(spec.tz) if spec.tz else PARIS
        grace = (spec.misfire_grace_min or DEFAULT_MISFIRE_MIN) * 60
        if spec.cron:
            jobs.append(ScheduledJob(name, "normal", _cron(spec.cron, tz), grace, spec))
            if spec.half_day:
                # Same cron day fields, time replaced by the half-day time.
                fields = spec.cron.split()
                hh, mm = spec.half_day.split(":")
                half_expr = " ".join([mm, hh, *fields[2:]])
                jobs.append(ScheduledJob(name, "half_day", _cron(half_expr, tz), grace, spec))
        elif spec.every_minutes_from and not spec.every_minutes:
            minutes = int(_resolve_path(params, spec.every_minutes_from) or 5)
            spec2 = spec.model_copy(update={"every_minutes": minutes})
            jobs.append(ScheduledJob(name, "normal", _window_interval(spec2, tz), grace, spec2))
        elif spec.every_minutes or spec.every_seconds:
            jobs.append(ScheduledJob(name, "normal", _window_interval(spec, tz), grace, spec))
        elif spec.premarket and spec.day:
            jobs.append(ScheduledJob(name, "premarket", _cron(spec.premarket["cron"], tz), grace, spec))
            jobs.append(ScheduledJob(name, "day", _cron(spec.day["cron"], tz), grace, spec))
        # jobs with `times_from` / `every_minutes_from` are resolved in their own phase
    return jobs
