"""``insider_archive`` (every evening, 21:35 Paris): managers' dealing declarations published by the AMF over the
last ``data.insiders.lookback_days`` days → ``insider_declarations`` (docs/34 §34.8). Archive only, for a future
pre-registered test: no signal, no score. Runs every day (``market_days_only: false``): the AMF publishes on
French business days, which are not Euronext's sessions (it publishes on some days the exchange is closed), and a
day missed is caught up by the look-back window — the archive is idempotent."""

from __future__ import annotations

from datetime import timedelta

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.insiders import archive, settings


def run(ctx: JobContext) -> int:
    today = ctx.run_date
    with db_session() as s:
        rep = archive(s, ctx.config, today - timedelta(days=settings(ctx.config)["lookback_days"]), today)
    ctx.note = rep.line()
    return rep.added
