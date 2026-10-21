"""``kpi_snapshot`` (18:10 Paris, market days): equity of the day per account → ``kpi_daily``
(docs/17 §17.5). Idempotent; an incomplete day is stored as such, never estimated."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.performance import snapshot_equity


def run(ctx: JobContext) -> int:
    with db_session() as s:
        return len(snapshot_equity(s, ctx.config, ctx.run_date))
