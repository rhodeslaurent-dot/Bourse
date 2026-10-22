# ruff: noqa: E501 — long French report lines
"""``brief_premarket`` (08:45 Paris, market days): the report of the day in 10 rubrics (docs/09 §9.2),
after the 07:50 engine — archived in ``daily_reports``, summarised on Telegram (P3) and sent in
full by e-mail when SMTP is configured."""

from __future__ import annotations

import logging

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.report import as_text, build, store, telegram_lines

log = logging.getLogger("bourse.jobs.daily_report")


def run(ctx: JobContext) -> int:
    with db_session() as s:
        content = build(s, ctx.config, ctx.run_date)
        store(s, ctx.run_date, content)
        if ctx.alerts is not None:
            from app.notify.base import Event

            ctx.alerts.emit(
                s,
                Event("P3", "daily_report", f"Rapport du {ctx.run_date:%d/%m}", telegram_lines(content)),
                ctx.mode,
                False,
            )
    try:
        from app.notify.email import send_email, smtp_settings

        if smtp_settings() is not None:
            send_email(f"[BOURSE-PILOT] Rapport du {ctx.run_date:%d/%m/%Y}", as_text(content))
    except Exception as exc:  # noqa: BLE001 — the report stays on the page
        log.warning("e-mail du rapport non envoyé : %s", exc.__class__.__name__)
    ctx.note = f"{len(content['rubriques'])} rubriques"
    return len(content["rubriques"])
