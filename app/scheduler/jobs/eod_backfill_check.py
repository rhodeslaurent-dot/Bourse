"""``eod_backfill_check`` (06:45): official EOD of the previous session for the whole universe
(one bulk call per exchange), replaces provisional closes, detects gaps per venue calendar,
recomputes ``features_daily``. Also used by ``python -m app.cli backfill``."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from sqlalchemy import select

from app.data.base import ProviderError, QuotaExceeded
from app.data.providers.eodhd import EodhdProvider
from app.db.models import Instrument
from app.db.session import db_session
from app.notify.base import Event
from app.scheduler.runner import JobContext
from app.services.market_data import compute_features_for_day, detect_gaps, upsert_eod

log = logging.getLogger("bourse.jobs.eod_backfill")


def backfill_history(provider: EodhdProvider, start: date, end: date, isins: list[str] | None = None) -> int:
    """EOD history for the referential, one instrument at a time. Progress is logged every 50
    instruments and committed as it goes; a symbol EODHD refuses is skipped (logged), and a quota
    exhaustion stops the run cleanly with what was already stored."""
    n = skipped = 0
    with db_session() as s:
        q = select(Instrument).where(Instrument.active.is_(True))
        if isins:
            q = q.where(Instrument.isin.in_(isins))
        insts = [i for i in s.scalars(q) if i.ticker_eodhd]
        log.info("backfill EOD %s → %s : %d instruments", start, end, len(insts))
        for k, inst in enumerate(insts, 1):
            try:
                bars = provider.fetch_eod([inst.ticker_eodhd], start, end)
            except QuotaExceeded as exc:
                log.warning("quota EODHD atteint après %d instruments : %s — reprendre plus tard", k - 1, exc)
                break
            except ProviderError as exc:
                skipped += 1
                log.warning("%s (%s) ignoré : %s", inst.ticker_eodhd, inst.isin, str(exc)[:120])
                continue
            n += upsert_eod(s, bars, {inst.ticker_eodhd: inst.isin})
            if k % 50 == 0 or k == len(insts):
                s.commit()
                log.info("backfill : %d/%d instruments, %d barres, %d ignorés", k, len(insts), n, skipped)
    return n


def run(ctx: JobContext) -> int:
    if not os.environ.get("EODHD_API_TOKEN"):
        return 0
    from app.data.providers.eodhd import eodhd_from

    provider = eodhd_from(ctx.config)
    n = 0
    gaps: dict[str, bool] = {}
    alerts = []
    with db_session() as s:
        insts = list(s.scalars(select(Instrument).where(Instrument.active.is_(True))))
        by_exchange: dict[str, list[Instrument]] = {}
        for i in insts:
            by_exchange.setdefault(i.exchange or "", []).append(i)
        for ex, group in by_exchange.items():
            mic = ctx.calendars.eodhd_to_mic.get(ex, ex)
            if (
                mic not in ctx.calendars.venues
                or mic not in ctx.open_mics
                and not ctx.calendars.get(mic).is_open(ctx.run_date)
            ):
                continue
            venue = ctx.calendars.get(mic)
            prev = venue.previous_session(ctx.run_date)
            try:
                bars = provider.fetch_eod_bulk(ex, prev)
            except Exception as exc:  # noqa: BLE001
                alerts.append(f"EOD {ex} du {prev}: {exc.__class__.__name__}")
                continue
            n += upsert_eod(s, bars, {i.ticker_eodhd: i.isin for i in group if i.ticker_eodhd})
            start = prev - timedelta(days=45)
            for i in group:
                g = detect_gaps(s, i.isin, venue, start, prev)
                if g.missing and i.in_universe:
                    gaps[i.isin] = True
                    for d in g.missing[-3:]:  # retry recent holes, never fill them synthetically
                        try:
                            n += upsert_eod(s, provider.fetch_eod([i.ticker_eodhd], d, d), {i.ticker_eodhd: i.isin})  # type: ignore[list-item]
                        except Exception:  # noqa: BLE001
                            pass
        universe = [i.isin for i in insts if i.in_universe]
        if universe:
            compute_features_for_day(s, ctx.run_date, universe, gaps)
        if (gaps or alerts) and ctx.alerts:
            ev = Event(
                "P4",
                "eod_gaps",
                f"EOD : {len(gaps)} instrument(s) avec trou, {len(alerts)} erreur(s)",
                [*alerts[:5], *(f"trou : {i}" for i in list(gaps)[:6])],
            )
            ctx.alerts.emit(s, ev, ctx.mode, False)
    return n
