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
        f" ; screener indisponible ({failed})" if failed else diagnosis(ctx.config, rows, n)
    )
    return n


def diagnosis(config, rows: list[SymbolRow], stored: int) -> str:  # type: ignore[no-untyped-def]
    """17/11/2026 — the job stored nothing for days without saying why: what is missing, in the note."""
    data = config.params.data
    tv_cfg = data.tradingview_screener or {}
    cols = {k: v for k, v in (tv_cfg.get("analyst_columns") or {}).items() if v}
    if not tv_cfg or not data.screener:
        return " ; screener non configuré (data.screener, data.tradingview_screener)"
    if not cols:
        return " ; colonnes d'analystes absentes de params.yaml (data.tradingview_screener.analyst_columns)"
    matched = sum(1 for r in rows if r.tv_ticker)
    out = f" ; {matched} valeur(s) trouvée(s) dans le screener"
    if matched and not stored:
        out += " ; aucune valeur numérique dans les colonnes " + ", ".join(sorted(set(cols.values())))
    out += coverage(rows)
    return out


def coverage(rows: list[SymbolRow], top: int = 8) -> str:
    """Per EODHD exchange, values found in the screener / values asked — the least covered first (17/11/2026: 1 754
    found out of 4 799; the screener returns at most ``limit_per_request`` rows per market, by volume)."""
    by: dict[str, list[int]] = {}
    for r in rows:
        c = by.setdefault(r.exchange, [0, 0])
        c[0] += 1 if r.tv_ticker else 0
        c[1] += 1
    low = sorted((v[0] / v[1], ex, v) for ex, v in by.items() if v[1] and v[0] < v[1])
    if not low:
        return ""
    return " ; couverture par place : " + ", ".join(f"{ex} {v[0]}/{v[1]}" for _, ex, v in low[:top])
