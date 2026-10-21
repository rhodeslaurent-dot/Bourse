"""``watchlist_refresh`` (07:15 Paris, market days, after ``eod_backfill_check``): momentum
watchlist recomputed every morning from the latest features (docs/18 §18.2). Indicators older
than the previous session are reported on the jobs_runs line, never recomputed from nothing."""

from __future__ import annotations

import logging

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.universe import refresh_watchlist_daily

log = logging.getLogger("bourse.jobs.watchlist_refresh")


def run(ctx: JobContext) -> int:
    with db_session() as s:
        n, day = refresh_watchlist_daily(s, ctx.config)
    expected = ctx.calendars.get("XPAR").previous_session(ctx.run_date)
    if day is None:
        ctx.note = "aucun indicateur calculé : watchlist inchangée"
    elif day < expected:
        ctx.note = f"indicateurs du {day:%d/%m} (dernière séance attendue {expected:%d/%m})"
    else:
        ctx.note = f"indicateurs du {day:%d/%m}"
    log.info("watchlist momentum: %s valeurs (%s)", n, ctx.note)
    return n
