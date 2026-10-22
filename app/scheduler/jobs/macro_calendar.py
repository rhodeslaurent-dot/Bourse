"""``macro_calendar`` (06:50 Paris, every day): economic calendar of the week and the next one
(ForexFactory public feed) → ``macro_events``, filtered on ``data.macro.calendar`` currencies and
impacts (docs/07 §7.9.5: events a stop does not cover)."""

from __future__ import annotations

from app.data.providers import forexfactory
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.macro import macro_settings, store_events


def run(ctx: JobContext) -> int:
    cal = macro_settings(ctx.config).get("calendar") or {}
    currencies = list(cal.get("currencies") or ["EUR", "USD"])
    impacts = list(cal.get("impacts") or ["High", "Medium"])
    events = forexfactory.fetch("thisweek") + forexfactory.fetch("nextweek")
    with db_session() as s:
        return store_events(s, events, currencies, impacts)
