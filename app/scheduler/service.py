"""APScheduler wiring: Postgres jobstore when available, coalesce, max_instances=1."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import LoadedConfig
from app.db.session import database_url, get_engine
from app.domain.calendar import MarketCalendars
from app.domain.timeutil import PARIS
from app.scheduler.registry import build_schedule
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
    config: LoadedConfig, calendars: MarketCalendars, alerts: object | None = None, mode_fn: object | None = None
) -> BackgroundScheduler:
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
