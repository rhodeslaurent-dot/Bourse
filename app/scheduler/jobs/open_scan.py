"""``open_scan_eu`` / ``open_scan_us`` (every 10 min after the opens, docs/06 D1, 18/11/2026): the values with a
catalyst since the previous close that open with a gap and an unusual volume get a card (page « Opportunités ») and
a Telegram alert — the engine sizes, blocks or marks « à vérifier » as for any card. See app/services/open_scan.py."""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services import open_scan


def _run(ctx: JobContext, mics: set[str]) -> int:
    with db_session() as s:
        n, note = open_scan.run(s, ctx.config, ctx.calendars, datetime.now(UTC), mics, ctx.alerts, ctx.mode)
    ctx.note = note
    return n


def run_eu(ctx: JobContext) -> int:
    return _run(ctx, open_scan.europe_mics(ctx.calendars))


def run_us(ctx: JobContext) -> int:
    return _run(ctx, open_scan.us_mics(ctx.calendars))
