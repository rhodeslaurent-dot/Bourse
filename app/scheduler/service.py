"""APScheduler wiring: Postgres jobstore when available, coalesce, max_instances=1, catch-up of the runs missed while
the application was down."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import LoadedConfig
from app.db.session import database_url, get_engine
from app.domain.calendar import MarketCalendars
from app.domain.timeutil import PARIS
from app.scheduler.registry import ScheduledJob, build_schedule
from app.scheduler.runner import execute_job

log = logging.getLogger("bourse.scheduler")

_state: dict[str, object] = {}


def _job_entry(name: str, variant: str) -> None:
    """Picklable entry point used by the jobstore."""
    config: LoadedConfig = _state["config"]  # type: ignore[assignment]
    calendars: MarketCalendars = _state["calendars"]  # type: ignore[assignment]
    alerts = _state.get("alerts")
    mode_fn = _state.get("mode_fn")
    mode = mode_fn() if callable(mode_fn) else "reunion"
    execute_job(
        name,
        config,
        calendars,
        variant=variant,
        scheduled_for=datetime.now(UTC),
        alerts=alerts,
        mode=mode,
        llm=_state.get("llm"),
    )


def create_scheduler(
    config: LoadedConfig,
    calendars: MarketCalendars,
    alerts: object | None = None,
    mode_fn: object | None = None,
    llm: object | None = None,
) -> BackgroundScheduler:
    _state["llm"] = llm  # ClaudeGateway or None (rules fallback)
    _state["config"] = config
    _state["calendars"] = calendars
    _state["alerts"] = alerts
    _state["mode_fn"] = mode_fn
    url = database_url()
    jobstore = (
        SQLAlchemyJobStore(engine=get_engine(), tablename="apscheduler_jobs")
        if url.startswith("postgresql")
        else MemoryJobStore()
    )
    sched = BackgroundScheduler(
        jobstores={"default": jobstore},
        job_defaults={"coalesce": True, "max_instances": 1},
        timezone=PARIS,
    )
    for sj in build_schedule(config.params):
        sched.add_job(
            _job_entry,
            trigger=sj.trigger,
            id=f"{sj.name}:{sj.variant}",
            name=sj.name,
            args=[sj.name, sj.variant],
            misfire_grace_time=sj.misfire_grace_seconds,
            replace_existing=True,
        )
        log.info("scheduled %s (%s) %s", sj.name, sj.variant, sj.trigger)
    return sched


# --- catch-up at start (16/11/2026) -----------------------------------------------------------------------------------
# APScheduler computes a job's next run from the moment the application starts: a run that fell due while it was down
# (a rebuild, a restart, the PC asleep) is lost, even inside its misfire grace — the morning of 16/11/2026 lost the
# closes (06:45), the regime (07:30) and the radar (06:40) to three restarts. At start, each job's latest due time
# inside its grace that left no line in ``jobs_runs`` is run once, in the order of the due times (the closes before
# the regime before the scan), with the date it was due for.

CATCH_UP_MIN_GRACE_S = 30 * 60  # jobs of a shorter grace run every few minutes: their next run comes soon enough
TOLERANT_GRACE_S = 600 * 60  # a job that may run 10 h late (night reads, weekly runs) waits for the urgent ones


def last_fire(trigger: Any, start: datetime, end: datetime) -> datetime | None:
    """The latest fire time of ``trigger`` in [start, end] (None when it does not fire there)."""
    t = trigger.get_next_fire_time(None, start)
    last = None
    for _ in range(100_000):
        if t is None or t > end:
            break
        last = t
        t = trigger.get_next_fire_time(t, t + timedelta(microseconds=1))
    return last


def missed_runs(
    jobs: list[ScheduledJob], now: datetime, last_started: dict[str, datetime]
) -> list[tuple[datetime, str, str]]:
    """(due time, job, variant) of the runs due inside their grace and not started since: the urgent ones first (the
    morning chain, in its due order: the closes before the regime before the scan), then the tolerant ones (a night
    read of the SEC may take an hour: it must not hold the closes back)."""
    out: dict[str, tuple[datetime, str, str]] = {}
    grace = {sj.name: sj.misfire_grace_seconds for sj in jobs}
    for sj in jobs:
        if sj.misfire_grace_seconds < CATCH_UP_MIN_GRACE_S:
            continue
        due = last_fire(sj.trigger, now - timedelta(seconds=sj.misfire_grace_seconds), now)
        if due is None:
            continue
        last = last_started.get(sj.name)
        if last is not None and last >= due:
            continue
        if sj.name not in out or due > out[sj.name][0]:  # one run per job (a half-day variant shares the name)
            out[sj.name] = (due, sj.name, sj.variant)
    return sorted(out.values(), key=lambda r: (grace[r[1]] >= TOLERANT_GRACE_S, r[0], r[1]))


def _last_started(names: list[str]) -> dict[str, datetime]:
    from sqlalchemy import func, select

    from app.db.models import JobRun
    from app.db.session import db_session
    from app.domain.timeutil import from_store

    with db_session() as s:
        rows = s.execute(
            select(JobRun.job, func.max(JobRun.started_at)).where(JobRun.job.in_(names)).group_by(JobRun.job)
        ).all()
    return {job: from_store(t) for job, t in rows if t is not None}


def _catch_up(runs: list[tuple[str, str, str, str]]) -> None:
    """Picklable entry: (job, variant, due ISO, run date ISO), one after the other in the given order."""
    config: LoadedConfig = _state["config"]  # type: ignore[assignment]
    calendars: MarketCalendars = _state["calendars"]  # type: ignore[assignment]
    mode_fn = _state.get("mode_fn")
    for name, variant, due, day in runs:
        log.warning("rattrapage au démarrage : %s (%s) dû le %s", name, variant, due)
        try:
            execute_job(
                name,
                config,
                calendars,
                run_date=date.fromisoformat(day),
                variant=variant,
                scheduled_for=datetime.fromisoformat(due),
                alerts=_state.get("alerts"),
                mode=mode_fn() if callable(mode_fn) else "reunion",
                llm=_state.get("llm"),
            )
        except Exception:  # noqa: BLE001 — one failed catch-up never stops the next ones
            log.exception("rattrapage de %s en échec", name)


def schedule_catch_up(sched: BackgroundScheduler, config: LoadedConfig, now: datetime | None = None) -> list[str]:
    """After ``start``: queue the missed runs as one one-off job (they run in order, on one thread). Never raises."""
    try:
        now = now or datetime.now(UTC)
        jobs = build_schedule(config.params)
        missed = missed_runs(jobs, now, _last_started(sorted({j.name for j in jobs})))
        if not missed:
            return []
        runs = [
            (n, v, due.astimezone(UTC).isoformat(), due.astimezone(PARIS).date().isoformat()) for due, n, v in missed
        ]
        sched.add_job(
            _catch_up,
            trigger="date",
            run_date=now + timedelta(seconds=30),
            id="catch_up_at_start",
            name="catch_up_at_start",
            args=[runs],
            misfire_grace_time=3600,
            replace_existing=True,
        )
        log.warning("rattrapage au démarrage prévu : %s", ", ".join(r[0] for r in runs))
        return [r[0] for r in runs]
    except Exception:  # noqa: BLE001 — the application starts anyway
        log.exception("rattrapage au démarrage impossible")
        return []
