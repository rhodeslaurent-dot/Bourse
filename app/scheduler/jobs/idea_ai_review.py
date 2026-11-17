"""``idea_ai_review`` (08:25 Paris, market days): step 2, option A — the day's best ideas analysed by an AI analyst
and an AI contradictor, the best ones arbitrated, beside Python's levels, in observation (docs/42, ADR-020)."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.idea_ai import run_day, settings


def run(ctx: JobContext) -> int:
    if not settings(ctx.config).enabled:
        ctx.note = "analyse multi-agent désactivée (idea_ai.enabled)"
        return 0
    with db_session() as s:
        rows = run_day(s, ctx.config, ctx.llm, ctx.run_date)
        by: dict[str, int] = {}
        for r in rows:
            key = f"arbitre:{r.chosen}" if r.status == "ok" and r.chosen else r.status
            by[key] = by.get(key, 0) + 1
        cost = sum(r.cost_usd or 0.0 for r in rows)
    ctx.note = (
        f"{len(rows)} idée(s) analysée(s) en observation : "
        + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "aucune idée du jour")
        + f" · {cost:.3f} $"
    )
    return len(rows)
