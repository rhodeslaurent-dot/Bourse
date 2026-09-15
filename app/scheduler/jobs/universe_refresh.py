"""``universe_refresh`` (Monday 06:30, every week): EODHD symbol lists of the P0/P1 exchanges +
screener facts (market cap, sector, next earnings) → referential, flags, snapshot, watchlist."""

from __future__ import annotations

import logging
import os
from datetime import date

from app.data.providers.eodhd import EodhdProvider
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.universe import RefreshReport, SymbolRow, refresh_universe

log = logging.getLogger("bourse.jobs.universe_refresh")


def symbol_rows_from_eodhd(provider: EodhdProvider, exchanges: list[str]) -> list[SymbolRow]:
    out: list[SymbolRow] = []
    for ex in exchanges:
        for r in provider.exchange_symbols(ex):
            isin = str(r.get("Isin") or "").strip().upper()
            if len(isin) != 12:
                continue
            out.append(
                SymbolRow(
                    isin=isin,
                    name=str(r.get("Name", "")),
                    code=str(r.get("Code", "")),
                    exchange=ex,
                    currency=str(r.get("Currency") or "EUR"),
                    country=_country(r.get("Country")),
                )
            )
    return out


def _country(name: object) -> str | None:
    table = {
        "france": "FR",
        "germany": "DE",
        "netherlands": "NL",
        "belgium": "BE",
        "italy": "IT",
        "spain": "ES",
        "sweden": "SE",
        "denmark": "DK",
        "finland": "FI",
        "norway": "NO",
        "portugal": "PT",
        "ireland": "IE",
        "austria": "AT",
        "poland": "PL",
        "luxembourg": "LU",
        "switzerland": "CH",
        "uk": "GB",
        "united kingdom": "GB",
        "usa": "US",
        "jersey": "JE",
        "bermuda": "BM",
    }
    return table.get(str(name or "").strip().lower())


def enrich_with_screener(rows: list[SymbolRow], config) -> list[SymbolRow]:  # type: ignore[no-untyped-def]
    """Market cap, sector, earnings date from the delayed screener (facts only, no prices used)."""
    tv_cfg = config.params.data.tradingview_screener or {}
    if not tv_cfg or not config.params.data.screener:
        return rows
    from app.data.providers.tradingview_screener import MARKETS_BY_EODHD, TradingViewScreener

    markets = sorted({MARKETS_BY_EODHD[r.exchange] for r in rows if r.exchange in MARKETS_BY_EODHD})
    try:
        tv = TradingViewScreener(
            min_interval_seconds=int(tv_cfg.get("min_interval_seconds", 60)),
            max_requests_per_scan=int(tv_cfg.get("max_requests_per_scan", 6)),
            limit_per_request=int(tv_cfg.get("limit_per_request", 1500)),
        )
        scan = tv.scan(markets[: tv.max_requests])
    except Exception as exc:  # noqa: BLE001
        log.warning("screener indisponible, univers sans capitalisation/secteur : %s", exc)
        return rows
    by_symbol = {r.ticker.split(":")[-1]: r for r in scan}
    for r in rows:
        sr = by_symbol.get(r.code)
        if sr is None:
            continue
        r.market_cap_eur = sr.market_cap
        r.sector = sr.sector
        r.earnings_next_date = sr.earnings_release_next_date
        r.tv_ticker = sr.ticker
    return rows


def run(ctx: JobContext) -> int:
    if not os.environ.get("EODHD_API_TOKEN"):
        log.info("EODHD_API_TOKEN absent : univers non rafraîchi")
        return 0
    u = ctx.config.params.universe or {}
    exchanges = list(u.get("markets_p0", [])) + list(u.get("markets_p1", []))
    rows = enrich_with_screener(symbol_rows_from_eodhd(EodhdProvider(), exchanges), ctx.config)
    with db_session() as s:
        rep: RefreshReport = refresh_universe(s, ctx.config, ctx.calendars, rows, date.today())
    log.info(
        "univers : %s inclus / %s ; +%s −%s ; watchlist %s",
        rep.included,
        rep.total,
        len(rep.added),
        len(rep.removed),
        rep.watchlist,
    )
    return rep.included
