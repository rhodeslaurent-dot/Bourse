"""``sources_daily`` (22:40 Paris, every day) — step 1 of the user's plan (lots S1 to S3, 15/11/2026).

1. the journal: every item received in the last 3 days by any source becomes a ``market_events`` row (idempotent);
2. the measure: what each value did 1, 5 and 20 sessions after each event, against its venue and its sector;
3. the health of every source (``source_health``), and one P3 message when a source falls silent.

Runs every day: letters, podcasts and forums also arrive on weekends, and a silent source must be seen on Monday.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta

from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.notify.base import Event
from app.scheduler.runner import JobContext
from app.services import event_journal, event_measure, source_health

log = logging.getLogger("bourse.jobs.sources_daily")

LOOKBACK_DAYS = 3


def run(ctx: JobContext) -> int:
    notes: list[str] = []
    since = datetime.combine(ctx.run_date - timedelta(days=LOOKBACK_DAYS), time.min, tzinfo=PARIS).astimezone(UTC)
    with db_session() as s:
        try:
            with s.begin_nested():
                rep = event_journal.collect(s, ctx.config, since)
                notes.append(rep.line())
        except Exception as exc:  # noqa: BLE001 — the health snapshot is still taken
            log.warning("journal des événements : %s", exc)
            notes.append(f"journal en échec ({exc.__class__.__name__})")
        try:
            with s.begin_nested():
                st = event_measure.settings(ctx.config)
                m = event_measure.measure(s, ctx.run_date, ctx.calendars, st=st)
                notes.append(m.line())
                for offset in st.control_offsets:  # the control windows (docs/40 §40.3)
                    event_measure.measure(s, ctx.run_date, ctx.calendars, st=st, offset=offset)
        except Exception as exc:  # noqa: BLE001
            log.warning("mesure des événements : %s", exc)
            notes.append(f"mesure en échec ({exc.__class__.__name__})")
        states = source_health.snapshot(s, ctx.run_date)
        notes.append(source_health.summary_line(states))
        late = source_health.newly_late(s, ctx.run_date, states)
        if late and ctx.alerts is not None:
            lines = [f"{x.label} : rien depuis le {x.last:%d/%m} ({x.feeder})" for x in late if x.last][:10]
            ev = Event("P3", "source_silent", f"{len(late)} source(s) muette(s) depuis trop longtemps", lines)
            ctx.alerts.emit(s, ev, ctx.mode, False)
    ctx.note = " ; ".join(notes)
    event_measure.warm_boards(ctx.config)  # the Sources page's tables ready before the next visit (18/11/2026)
    return len(states)
