# ruff: noqa: E501 — long French report lines
"""``portfolio_review`` (Saturday 08:30 Paris): review of every position (docs/22 §22.5) stored in
``portfolio_reviews`` and summarised on Telegram (P3, ≤ 12 lines)."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.portfolio_review import run_review, telegram_summary


def run(ctx: JobContext) -> int:
    with db_session() as s:
        rev = run_review(s, ctx.config, trigger="weekly")
        if ctx.alerts is not None:
            from app.notify.base import Event

            ctx.alerts.emit(
                s, Event("P3", "portfolio_review", "Revue de portefeuille", telegram_summary(rev)), ctx.mode, False
            )
        n = len(rev.lines or [])
    ctx.note = f"{n} position(s) revue(s)"
    return n
