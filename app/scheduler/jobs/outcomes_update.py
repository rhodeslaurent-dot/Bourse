"""``outcomes_update`` (07:20 Paris, market days): result of every past proposal, taken or not,
on official closes (docs/18 §18.4). Idempotent; final outcomes are never recomputed."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.outcomes import update_outcomes


def run(ctx: JobContext) -> int:
    with db_session() as s:
        return update_outcomes(s, ctx.config, ctx.calendars)
