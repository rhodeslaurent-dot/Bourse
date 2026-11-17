"""Build APScheduler triggers from ``params.yaml › jobs`` (Europe/Paris unless ``tz``)."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger

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
    "intraday_reeval": "app.scheduler.jobs.intraday_reeval:run",
    "universe_refresh": "app.scheduler.jobs.universe_refresh:run",
    "eod_backfill_check": "app.scheduler.jobs.eod_backfill_check:run",
    "daily_regime": "app.scheduler.jobs.daily_regime:run",
    "news_scan": "app.scheduler.jobs.news_scan:run",
    "imap_poll": "app.scheduler.jobs.imap_poll:run",
    "saxo_token_keepalive": "app.scheduler.jobs.saxo_token_keepalive:run",
    "kpi_snapshot": "app.scheduler.jobs.kpi_snapshot:run",
    "watchlist_refresh": "app.scheduler.jobs.watchlist_refresh:run",
    "outcomes_update": "app.scheduler.jobs.outcomes_update:run",
    "weekly_review": "app.scheduler.jobs.weekly_review:run",
    "letters_review": "app.scheduler.jobs.letters_review:run",
    "fx_refresh": "app.scheduler.jobs.fx_refresh:run",
    "macro_snapshot": "app.scheduler.jobs.macro_snapshot:run",
    "macro_calendar": "app.scheduler.jobs.macro_calendar:run",
    "opportunity_scan": "app.scheduler.jobs.opportunity_scan:run",
    "portfolio_review": "app.scheduler.jobs.portfolio_review:run",
    "fixing_card": "app.scheduler.jobs.fixing_card:run",
    "fundamentals_refresh": "app.scheduler.jobs.fundamentals_refresh:run",
    "short_positions_refresh": "app.scheduler.jobs.short_positions_refresh:run",
    "weak_signals": "app.scheduler.jobs.weak_signals:run",
    "social_watch": "app.scheduler.jobs.social_watch:run",
    "forum_history": "app.scheduler.jobs.forum_history:run",
    "shadow_signals": "app.scheduler.jobs.shadow_signals:run",
    "earnings_refresh": "app.scheduler.jobs.earnings_refresh:run",
    "insider_archive": "app.scheduler.jobs.insider_archive:run",
    "committee_review": "app.scheduler.jobs.committee_review:run",
    "backtest_weekly": "app.scheduler.jobs.backtest_weekly:run",
    "brief_premarket": "app.scheduler.jobs.daily_report:run",
    "sources_daily": "app.scheduler.jobs.sources_daily:run",
    "consensus_daily": "app.scheduler.jobs.consensus_daily:run",
    "filings_daily": "app.scheduler.jobs.filings_daily:run",
    "us_filings": "app.scheduler.jobs.us_filings:run",
    "position_ai_review": "app.scheduler.jobs.position_ai_review:run",
    "idea_ai_review": "app.scheduler.jobs.idea_ai_review:run",
}


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    variant: str
    trigger: BaseTrigger
    misfire_grace_seconds: int
    spec: JobSpec


_CRONTAB_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_WEEK_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _day_number(token: str) -> int:
    t = token.strip().lower()
    if t[:3] in _CRONTAB_DAYS:
        return _CRONTAB_DAYS.index(t[:3])
    n = int(t)
    if not 0 <= n <= 7:
        raise ValueError(f"jour de semaine hors de 0-7 : {token}")
    return n % 7


def crontab_days(field: str) -> str:
    """The day-of-week field of a standard crontab (0 or 7 = Sunday, 1 = Monday … 6 = Saturday), as APScheduler's day
    names. APScheduler 3 reads the numbers with 0 = **Monday**, and its ``from_crontab`` passes them unchanged: until
    16/11/2026 « 1-5 » ran Tuesday to Saturday — no closes, regime, news or scan on Mondays — and the Sunday backtest
    « 0 » ran on Monday during the session."""
    f = field.strip()
    if f in ("*", "?"):
        return "*"
    days: set[int] = set()
    for part in f.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part in ("*", ""):
            lo, hi = 0, 6
        elif "-" in part:
            a, b = part.split("-", 1)
            lo, hi = _day_number(a), _day_number(b)
            if b.strip() == "7":  # « 1-7 »: Monday to Sunday
                hi = 7
            if hi < lo:
                raise ValueError(f"plage de jours inversée : {part}")
        else:
            lo = hi = _day_number(part)
            if step > 1:  # « 1/2 »: from that day to the end of the week
                hi = 6
        days.update(d % 7 for d in range(lo, hi + 1, step))
    names = {_CRONTAB_DAYS[d] for d in days}
    return ",".join(n for n in _WEEK_ORDER if n in names)


def _cron(expr: str, tz: ZoneInfo) -> CronTrigger:
    """A standard 5-field crontab (minute hour day month day-of-week), its day of week read the crontab way."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expression cron à 5 champs attendue : {expr!r}")
    minute, hour, day, month, dow = fields
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=crontab_days(dow), timezone=tz)


def _window_interval(spec: JobSpec, tz: ZoneInfo) -> BaseTrigger:
    """``every_minutes``/``every_seconds`` inside ``window_local`` on ``days`` → cron with ranges."""
    days = spec.days or "1-5"
    start, end = spec.window_local or ["09:00", "17:40"]
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    # cron day_of_week uses 0=mon; params use ISO 1=mon → convert ranges like "1-5" → "mon-fri"
    dow = _iso_days_to_cron(days)
    hour = f"{sh}-{eh}"
    if spec.every_seconds and spec.every_seconds < 60:
        # sub-minute cadence (intraday_reeval, 15 s) kept inside the window and the days, like the minute jobs
        return CronTrigger(second=f"*/{spec.every_seconds}", minute="*", hour=hour, day_of_week=dow, timezone=tz)
    minutes = spec.every_minutes or (spec.every_seconds or 60) // 60 or 1
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
