"""``fundamentals_refresh`` (every night, 05:10 Paris since lot S4 of 15/11/2026; Sunday before): free fundamentals
and analyst consensus of the universe and the held positions (Yahoo, throttled) → ``fundamentals`` (docs/22 §22.7),
``data.fundamentals.max_items`` values per night by rotation (held first, then never read, then the oldest)."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.fundamentals import refresh


def run(ctx: JobContext) -> int:
    f = (getattr(ctx.config.params.data, "fundamentals", None) or {}) if ctx.config.params.data else {}
    with db_session() as s:
        rep = refresh(s, ctx.run_date, int(f.get("max_items", 800)), float(f.get("min_interval_s", 1.0)))
    ctx.note = ", ".join(f"{k} {v}" for k, v in rep.items())
    return rep["ok"]
