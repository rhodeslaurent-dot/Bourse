"""``letters_review`` (19:15 Paris, market days) — evening catch-up (docs/20 §20.1): letters are
reviewed at reception by ``imap_poll``; this job reviews those left over (LLM unavailable, budget)
and sends the day's digest (P3, ≤ 12 lines)."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.letters_review import digest_event, params_of, recommendations, run_review


def run(ctx: JobContext) -> int:
    p = params_of(ctx.config)
    if p is None:
        ctx.note = "groupe letters_review absent de params.yaml : revue désactivée"
        return 0
    with db_session() as s:
        rev = run_review(s, ctx.config, ctx.llm, trigger="daily", alerts=ctx.alerts, mode=ctx.mode)
        recs = recommendations(s, ctx.config, rev.day)
        ctx.note = f"rattrapage : {len(rev.item_ids or [])} lettre(s) · {rev.status} · {len(recs)} préconisation(s)"
        if p.telegram_digest and ctx.alerts is not None and recs:
            ctx.alerts.emit(s, digest_event(rev, recs), ctx.mode, False)
        return len(recs)
