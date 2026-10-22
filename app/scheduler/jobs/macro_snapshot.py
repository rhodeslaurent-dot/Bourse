"""``macro_snapshot`` (07:25 and 17:55 Paris, market days): free macro dashboard series (Brent, VIX,
gold, EUR/USD, 10-year yields, indices) → ``macro_points`` with source and status (docs/22 §22.7).
A missing series is reported in the job note, never estimated."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.macro import snapshot


def run(ctx: JobContext) -> int:
    with db_session() as s:
        rep = snapshot(s, ctx.config, ctx.run_date)
    ctx.note = rep.note()
    return rep.stored
