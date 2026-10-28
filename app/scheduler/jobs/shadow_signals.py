"""``shadow_signals`` (07:15, after the official closes of eod_backfill_check): prospective shadow tracking of
registration n° 2 on the European venues (docs/33 §33.3) — stored, never shown."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.shadow import record_shadow_signals


def run(ctx: JobContext) -> int:
    with db_session() as s:
        out = record_shadow_signals(s, ctx.config, ctx.calendars, ctx.run_date)
    ctx.note = "signaux en ombre : " + ", ".join(f"{k} {v}" for k, v in out.items())
    return sum(out.values())
