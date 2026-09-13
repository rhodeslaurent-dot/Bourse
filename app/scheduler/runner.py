"""Run one job: gate on the venue calendar, write ``jobs_runs``, ping the watchdog."""

from __future__ import annotations

import importlib
import logging
import os
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.config import LoadedConfig
from app.db.models import JobRun
from app.db.session import db_session
from app.domain.calendar import MarketCalendars
from app.domain.timeutil import PARIS
from app.scheduler.gating import should_run
from app.scheduler.registry import IMPLEMENTED_JOBS

log = logging.getLogger("bourse.jobs")


@dataclass
class JobContext:
    config: LoadedConfig
    calendars: MarketCalendars
    run_date: date
    open_mics: tuple[str, ...]
    variant: str
    notify: Callable[..., Any] | None = None


JobFn = Callable[[JobContext], int]


def resolve_job(name: str) -> JobFn:
    target = IMPLEMENTED_JOBS[name]
    module, fn = target.split(":")
    return getattr(importlib.import_module(module), fn)


def watchdog_ping(job: str, ok: bool) -> None:
    """Dead man's switch (docs/14 §14.2): ping healthchecks.io if configured, never raise."""
    key = os.environ.get("HEALTHCHECKS_PING_KEY")
    if not key:
        return
    base = os.environ.get("HEALTHCHECKS_BASE_URL", "https://hc-ping.com").rstrip("/")
    url = f"{base}/{key}/{job}" + ("" if ok else "/fail") + "?create=1"
    try:
        httpx.get(url, timeout=10)
    except Exception as exc:  # noqa: BLE001 — a watchdog failure must never break a job
        log.warning("watchdog ping failed for %s: %s", job, exc)


def execute_job(
    name: str,
    config: LoadedConfig,
    calendars: MarketCalendars,
    run_date: date | None = None,
    variant: str = "normal",
    mics: list[str] | None = None,
    scheduled_for: datetime | None = None,
    fn: JobFn | None = None,
) -> JobRun:
    spec = config.params.jobs.specs()[name]
    run_date = run_date or datetime.now(PARIS).date()
    started = datetime.now(UTC)
    decision = should_run(name, spec, run_date, calendars, mics=mics, variant=variant)
    with db_session() as s:
        run = JobRun(
            job=name,
            scheduled_for=scheduled_for,
            started_at=started,
            status="running",
            params_version=config.params.version,
        )
        s.add(run)
        s.flush()
        if not decision.run:
            run.status = "skipped"
            run.note = decision.reason
            run.ended_at = datetime.now(UTC)
            log.info("job %s skipped: %s", name, decision.reason)
            watchdog_ping(name, True)
            return run
        ctx = JobContext(config, calendars, run_date, decision.open_mics, variant)
        try:
            job_fn = fn or resolve_job(name)
            rows = job_fn(ctx)
            run.status = "ok"
            run.rows = rows
            run.note = decision.reason
        except Exception as exc:  # noqa: BLE001 — recorded, then reported
            run.status = "error"
            run.error = f"{exc}\n{traceback.format_exc()[-2000:]}"
            log.exception("job %s failed", name)
        finally:
            run.ended_at = datetime.now(UTC)
        watchdog_ping(name, run.status == "ok")
        return run
