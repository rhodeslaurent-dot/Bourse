"""``position_ai_review`` (19:35 Paris, market days): step 2, option B — each held position reviewed by an AI analyst
and a contradictor after Python's own review, in observation (docs/42). Budget-capped, idempotent; never applied."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.position_ai import run_day, settings


def run(ctx: JobContext) -> int:
    if not settings(ctx.config).enabled:
        ctx.note = "revue IA des positions désactivée (position_ai.enabled)"
        return 0
    with db_session() as s:
        rows = run_day(s, ctx.config, ctx.llm, ctx.run_date)
        by: dict[str, int] = {}
        for r in rows:
            key = r.action if r.status == "ok" else r.status
            by[str(key)] = by.get(str(key), 0) + 1
        cost = sum(r.cost_usd or 0.0 for r in rows)
    ctx.note = (
        f"{len(rows)} position(s) revue(s) en observation : "
        + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "aucune position avec un cours")
        + f" · {cost:.3f} $"
    )
    return len(rows)
