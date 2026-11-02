# ruff: noqa: E501 — long French report lines
"""``brief_premarket`` (08:45 Paris, market days): the report of the day in 10 rubrics (docs/09 §9.2),
after the 07:50 engine — archived in ``daily_reports``. Since lot R1 (docs/38, 02/11/2026) the Telegram message
(P3) is the « Revue du jour » (verdict, what to do, the ideas' decisions) and the e-mail starts with it, the
10 rubrics following."""

from __future__ import annotations

import logging

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services import daily_review
from app.services.report import as_text, build, store

log = logging.getLogger("bourse.jobs.daily_report")


def run(ctx: JobContext) -> int:
    with db_session() as s:
        content = build(s, ctx.config, ctx.run_date)
        store(s, ctx.run_date, content)
        review = daily_review.build(s, ctx.config, ctx.run_date)
        if ctx.alerts is not None:
            from app.notify.base import Event

            ctx.alerts.emit(
                s,
                Event("P3", "daily_report", f"Revue du {ctx.run_date:%d/%m}", daily_review.telegram_lines(review)),
                ctx.mode,
                False,
            )
    try:
        from app.notify.email import send_email, smtp_settings

        if smtp_settings() is not None:
            body = daily_review.as_text(review) + "\n\n" + "—" * 40 + "\n\n" + as_text(content)
            send_email(f"[BOURSE-PILOT] Revue du {ctx.run_date:%d/%m/%Y} — {review['verdict']['titre']}", body)
    except Exception as exc:  # noqa: BLE001 — the report stays on the page
        log.warning("e-mail du rapport non envoyé : %s", exc.__class__.__name__)
    ctx.note = f"{len(content['rubriques'])} rubriques"
    return len(content["rubriques"])
