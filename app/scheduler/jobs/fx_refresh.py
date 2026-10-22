"""``fx_refresh`` (16:45 Paris, every day): ECB euro reference rates → ``fx_rates`` (EURxxx). The
first run loads 90 days. Needed to convert non-euro market caps and positions (docs/22 §22.6)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.data.providers import ecb_fx
from app.db.models import FxRate
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.macro import store_fx


def run(ctx: JobContext) -> int:
    with db_session() as s:
        empty = not s.scalar(select(func.count(FxRate.id)))
        return store_fx(s, ecb_fx.fetch("90d" if empty else "daily"))
