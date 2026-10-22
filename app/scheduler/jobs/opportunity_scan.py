"""``opportunity_scan`` (07:50 Paris, market days, after the overnight EOD load): the proposal
engine on the whole universe (docs/22 §22.4), then the thesis of the top of the ranking
(``ranking.home_top_n``) and a P2 for the top ``ranking.notify_top_n`` unblocked BUY."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.engine import enabled, notify_top, scan, todays_ranked
from app.services.thesis import write_top_theses


def run(ctx: JobContext) -> int:
    if not enabled(ctx.config):
        ctx.note = "moteur désactivé : blocs admission / detectors / scoring absents de params.yaml"
        return 0
    with db_session() as s:
        rep = scan(s, ctx.config, ctx.run_date)
        top = int((getattr(ctx.config.params, "ranking", None) or {}).get("home_top_n", 10))
        n_th = write_top_theses(s, ctx.config, ctx.llm, todays_ranked(s, ctx.run_date), top)
        sent = notify_top(s, ctx.config, ctx.alerts, ctx.mode, ctx.run_date) if ctx.alerts is not None else 0
    ctx.note = f"{rep.note()} · {n_th} thèse(s) · {sent} notification(s)"
    return rep.created
