"""``universe_refresh`` (Monday 06:30, every week): EODHD symbol lists of the P0/P1 exchanges +
screener facts (market cap, sector, next earnings) → referential, flags, snapshot, watchlist."""

from __future__ import annotations

import logging
import os
from datetime import date

from app.data.base import ProviderError
from app.data.providers.eodhd import EodhdProvider
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.universe import RefreshReport, SymbolRow, refresh_universe

log = logging.getLogger("bourse.jobs.universe_refresh")


def symbol_rows_from_eodhd(
    provider: EodhdProvider, exchanges: list[str], errors: dict[str, str] | None = None
) -> list[SymbolRow]:
    """Symbol lists of the given EODHD exchange codes. An exchange that fails (unknown code, outage)
    is recorded in ``errors`` and skipped: one market never blocks the others."""
    out: list[SymbolRow] = []
    for ex in exchanges:
        try:
            symbols = provider.exchange_symbols(ex)
        except ProviderError as exc:
            if errors is None:
                raise
            errors[ex] = str(exc)[:160]
            log.warning("place EODHD %s ignorée : %s", ex, errors[ex])
            continue
        for r in symbols:
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


def _log_exchange_hints(provider: EodhdProvider, errors: dict[str, str]) -> None:
    """For an unknown exchange code, log the European codes EODHD actually exposes."""
    if not any("404" in v or "Not Found" in v for v in errors.values()):
        return
    try:
        codes = [
            f"{e.get('Code')} ({e.get('Name')}, {e.get('Country')})"
            for e in provider.exchanges()
            if str(e.get("Currency", "")) in ("EUR", "SEK", "DKK", "NOK", "CHF", "GBP", "PLN")
        ]
        log.warning("codes de places EODHD disponibles en Europe : %s", "; ".join(codes[:40]))
    except ProviderError as exc:  # diagnostics only
        log.info("liste des places EODHD indisponible : %s", exc)


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
        for r in rows:
            r.extra["screener_error"] = exc.__class__.__name__
        return rows
    tv_exchange = {
        "france": "EURONEXT",
        "netherlands": "EURONEXT",
        "belgium": "EURONEXT",
        "germany": "XETR",
        "italy": "MIL",
        "spain": "BME",
    }
    by_key = {r.ticker: r for r in scan}  # EXCHANGE:SYMBOL — never merged across venues (M21)
    for r in rows:
        market = MARKETS_BY_EODHD.get(r.exchange)
        sr = by_key.get(f"{tv_exchange.get(market or '', '')}:{r.code}") if market else None
        if sr is None:
            continue
        # market_cap_basic is expressed in the instrument's quote currency (to be confirmed in
        # qualification, docs/providers/tradingview_screener.md); non-EUR caps are left unset.
        r.market_cap_eur = sr.market_cap if r.currency == "EUR" else None
        r.sector = sr.sector
        r.earnings_next_date = sr.earnings_release_next_date
        r.tv_ticker = sr.ticker
    return rows


def run(ctx: JobContext) -> int:
    if not os.environ.get("EODHD_API_TOKEN"):
        log.warning("EODHD_API_TOKEN absent : univers non rafraîchi (env-setup eodhd puis recréer le conteneur)")
        return 0
    u = ctx.config.params.universe or {}
    from app.data.providers.eodhd import eodhd_from

    provider = eodhd_from(ctx.config)
    exchanges, skipped = [], []
    for ex in list(u.get("markets_p0", [])) + list(u.get("markets_p1", [])):
        mic = ctx.calendars.eodhd_to_mic.get(ex)
        if mic in ctx.calendars.venues:
            exchanges.append(ex)
        else:
            skipped.append(ex)  # ADR-005 M7: a market without a calendar is not followed yet
    if skipped:
        log.warning("places sans calendrier, non chargées : %s (encoder market_calendars.yaml)", ", ".join(skipped))
    errors: dict[str, str] = {}
    rows = enrich_with_screener(symbol_rows_from_eodhd(provider, exchanges, errors), ctx.config)
    if errors:
        _log_exchange_hints(provider, errors)
    if not rows:
        raise ProviderError("aucun symbole chargé : " + " ; ".join(f"{k}: {v}" for k, v in errors.items()))
    log.info("%d symboles chargés depuis EODHD (%s)", len(rows), ", ".join(e for e in exchanges if e not in errors))
    with db_session() as s:
        rep: RefreshReport = refresh_universe(s, ctx.config, ctx.calendars, rows, date.today())
        if errors and ctx.alerts is not None:
            from app.notify.base import Event

            ctx.alerts.emit(
                s,
                Event(
                    "P4",
                    "universe_exchange_unavailable",
                    f"{len(errors)} place(s) EODHD non chargée(s)",
                    [f"{k} : {v[:100]}" for k, v in errors.items()][:6],
                ),
                ctx.mode,
                False,
            )
        if rows and rows[0].extra.get("screener_error") and ctx.alerts is not None:
            from app.notify.base import Event

            ctx.alerts.emit(
                s,
                Event(
                    "P4",
                    "screener_unavailable",
                    "Univers rafraîchi sans le screener",
                    [
                        "capitalisations et secteurs de la semaine précédente conservés (périmés)",
                        rows[0].extra["screener_error"],
                    ],
                ),
                ctx.mode,
                False,
            )
    log.info(
        "univers : %s inclus / %s ; +%s −%s ; watchlist %s",
        rep.included,
        rep.total,
        len(rep.added),
        len(rep.removed),
        rep.watchlist,
    )
    if rep.reasons:
        top = sorted(rep.reasons.items(), key=lambda kv: -kv[1])
        log.info("motifs d'exclusion (un instrument peut en cumuler) : %s", " ; ".join(f"{k} : {v}" for k, v in top))
    if rep.watchlist == 0 and rep.included:
        log.info("watchlist vide : les indicateurs du jour manquent — lancer eod_backfill_check puis relancer ce job")
    return rep.included
