"""``opportunity_scan`` (07:50 Paris, market days, after the overnight EOD load): the proposal
engine on the whole universe (docs/22 §22.4, lot 2.3). Proposals are ranked; each one carries its
frozen snapshot. Nothing is sent from here: notifications of the top of the ranking are lot 2.4."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.engine import enabled, scan


def run(ctx: JobContext) -> int:
    if not enabled(ctx.config):
        ctx.note = "moteur désactivé : blocs admission / detectors / scoring absents de params.yaml"
        return 0
    with db_session() as s:
        rep = scan(s, ctx.config, ctx.run_date)
    ctx.note = rep.note()
    return rep.created
