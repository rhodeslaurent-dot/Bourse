"""``committee_review`` (08:10 Paris, market days): the investment committee deliberates on the day's top
BUY and on the proposals eligible to an override, in observation mode (docs/24 v2). Budget-capped,
idempotent; a verdict never changes a proposal."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.committee import run_day, settings


def run(ctx: JobContext) -> int:
    st = settings(ctx.config)
    if not st.enabled:
        ctx.note = "comité désactivé (committee.enabled)"
        return 0
    with db_session() as s:
        runs = run_day(s, ctx.config, ctx.llm, ctx.run_date)
        by: dict[str, int] = {}
        for r in runs:
            key = r.action_comite if r.status == "ok" else r.status
            by[str(key)] = by.get(str(key), 0) + 1
        cost = sum(r.cost_usd or 0.0 for r in runs)
    ctx.note = (
        f"{len(runs)} délibération(s) en observation : "
        + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "aucune proposition éligible")
        + f" · {cost:.3f} $"
    )
    return len(runs)
