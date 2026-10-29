"""``earnings_refresh`` (Saturday 05:40 Paris, weekly): announcements of the last months and the coming ones for the
universe (EODHD calendar if the plan includes it, else Yahoo, throttled) → ``earnings_events`` (docs/34 §34.4).
The next announcement date also fills ``instruments.earnings_next_date`` when the screener gave none (R14 freeze
before a publication, the score's earnings criterion)."""

from __future__ import annotations

from datetime import timedelta

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.earnings import fill_next_dates, load_history


def run(ctx: JobContext) -> int:
    today = ctx.run_date
    with db_session() as s:
        rep = load_history(s, ctx.config, ctx.calendars, today - timedelta(days=120), today + timedelta(days=150),
                           yahoo_pages=1)  # fmt: skip
        filled = fill_next_dates(s, today)
    ctx.note = (
        f"{rep.values} valeurs, {rep.added} publication(s) ajoutée(s), {filled} prochaine(s) date(s) renseignée(s)"
        + (f" · {'; '.join(rep.notes)}" if rep.notes else "")
    )
    return rep.added
