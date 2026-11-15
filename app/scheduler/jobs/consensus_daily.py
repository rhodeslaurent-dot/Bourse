"""``consensus_daily`` (22:20 Paris, Monday to Friday) — lot S4 (step 1 of the user's plan, 15/11/2026).

The analysts' consensus of every value of the universe and of the held positions (delayed screener: number of
analysts, average target, rating as published) stored **every evening** instead of once a week, so that a change of
the average target is dated to the day (``market_events``, lot S2). Facts as published, never interpreted."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.session import db_session
from app.scheduler.jobs.universe_refresh import enrich_with_screener, store_analyst_consensus
from app.scheduler.runner import JobContext
from app.services.universe import SymbolRow

log = logging.getLogger("bourse.jobs.consensus_daily")


def symbol_rows(insts: list) -> list[SymbolRow]:  # type: ignore[type-arg]
    """One screener row per value from its stored EODHD ticker (``CODE.EXCHANGE``); a value without one is skipped."""
    out = []
    for i in insts:
        t = i.ticker_eodhd or ""
        if "." not in t:
            continue
        code, ex = t.rsplit(".", 1)
        out.append(SymbolRow(isin=i.isin, name=i.name, code=code, exchange=ex, currency=i.currency or "EUR"))
    return out


def run(ctx: JobContext) -> int:
    from app.db.models import Instrument, PositionRow

    with db_session() as s:
        held = {p.isin for p in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None)))}
        insts = list(
            s.scalars(select(Instrument).where((Instrument.in_universe.is_(True)) | (Instrument.isin.in_(held))))
        )
        rows = enrich_with_screener(symbol_rows(insts), ctx.config)
        failed = next((r.extra.get("screener_error") for r in rows if r.extra.get("screener_error")), None)
        n = store_analyst_consensus(s, rows, ctx.run_date)
    ctx.note = f"{n} consensus enregistré(s) sur {len(rows)} valeur(s)" + (
        f" ; screener indisponible ({failed})" if failed else ""
    )
    return n
