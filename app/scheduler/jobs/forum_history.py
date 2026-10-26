"""``forum_history`` (every 30 min, 00:30–05:30 Paris, every night until done): the slow reading of the forum
history of the Paris values (docs/26, user decision of 26/10/2026), for the measure of « buzz précoce »."""

from __future__ import annotations

import httpx

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.forum_history import run_pass


def run(ctx: JobContext) -> int:
    with httpx.Client(follow_redirects=True) as client, db_session() as s:
        notes = run_pass(s, ctx.config, client)
    ctx.note = " ; ".join(notes)
    return 0
