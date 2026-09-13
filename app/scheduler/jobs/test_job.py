"""Phase 0 test job (A0.3 / A0.5): proves scheduling, calendar gating, jobs_runs and watchdog."""

from __future__ import annotations

import logging

from app.scheduler.runner import JobContext

log = logging.getLogger("bourse.jobs.test_job")


def run(ctx: JobContext) -> int:
    log.info("test_job ran for %s on venues %s (variant %s)", ctx.run_date, ctx.open_mics, ctx.variant)
    return len(ctx.open_mics)
