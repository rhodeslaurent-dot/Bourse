"""``shadow_signals`` (07:15, after the official closes of eod_backfill_check): prospective shadow tracking of the
frozen cells of registrations n° 2 (docs/33 §33.3), n° 3 (docs/34 §34.5) and n° 5 (docs/34 §34.12) on the
European venues — stored, never shown."""

from __future__ import annotations

import logging

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.shadow import PLAN, PLAN_REG3, PLAN_REG5, record_shadow_signals

log = logging.getLogger("bourse.jobs.shadow_signals")


def run(ctx: JobContext) -> int:
    notes, total = [], 0
    for name, plan in (("n° 2", PLAN), ("n° 3", PLAN_REG3), ("n° 5", PLAN_REG5)):
        try:
            with db_session() as s:
                out = record_shadow_signals(s, ctx.config, ctx.calendars, ctx.run_date, plan=plan)
        except Exception as exc:  # noqa: BLE001 — one plan failing never stops the other's series
            log.warning("ombre %s : %s", name, exc.__class__.__name__, exc_info=True)
            notes.append(f"{name} en échec ({exc.__class__.__name__})")
            continue
        total += sum(out.values())
        notes.append(f"{name} : " + ", ".join(f"{k} {v}" for k, v in out.items()))
    ctx.note = "signaux en ombre — " + " ; ".join(notes)
    return total
