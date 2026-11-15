"""``filings_daily`` (21:50 Paris, every day) — lot S4 (step 1 of the user's plan, 15/11/2026).

The official filings of the AMF feed (info-financiere.gouv.fr: results, guidance, M&A, capital operations…) read
**every evening** over the last ``data.filings.daily_lookback_days`` days instead of by hand (``filings-archive``),
so that each filing enters the events journal on its day. Metadata only (type, title, time, ISIN), idempotent."""

from __future__ import annotations

from datetime import timedelta

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.filings import archive_api, settings


def run(ctx: JobContext) -> int:
    days = settings(ctx.config)["daily_lookback_days"]
    with db_session() as s:
        rep = archive_api(s, ctx.config, ctx.run_date - timedelta(days=days), ctx.run_date)
    ctx.note = rep.line()
    return rep.added
