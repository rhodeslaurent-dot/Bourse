"""``universe_refresh`` (Monday 06:30, every week): EODHD symbol lists of the P0/P1/P2 exchanges +
screener facts (market cap, sector, next earnings) → referential, flags, snapshot, watchlist."""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta

from app.data.base import ProviderError, QuotaExceeded
from app.data.providers.eodhd import EodhdProvider
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.universe import RefreshReport, SymbolRow, refresh_universe

log = logging.getLogger("bourse.jobs.universe_refresh")
VENUE_FILTER = {"US": {"NYSE", "NASDAQ", "NYSE ARCA", "NYSE MKT", "BATS"}}


def symbol_rows_from_eodhd(
    provider: EodhdProvider,
    exchanges: list[str],
    errors: dict[str, str] | None = None,
    include_etf: bool = False,
    crypto_isins: set[str] | None = None,
) -> list[SymbolRow]:
    """Symbol lists of the given EODHD exchange codes. An exchange that fails (unknown code, outage)
    is recorded in ``errors`` and skipped: one market never blocks the others."""
    out: list[SymbolRow] = []
    for ex in exchanges:
        try:
            types: tuple[str, ...] = ("common stock", "stock", "")
            if include_etf:
                types += ("etf",)
            if crypto_isins:
                types += ("etf", "etc", "etn")  # listed crypto ETPs (docs/22 §22.5 rank 3)
            symbols = (
                provider.exchange_symbols(ex, types)
                if types != ("common stock", "stock", "")
                else provider.exchange_symbols(ex)
            )
        except ProviderError as exc:
            if errors is None:
                raise
            errors[ex] = str(exc)[:160]
            log.warning("place EODHD %s ignorée : %s", ex, errors[ex])
            continue
        venues = VENUE_FILTER.get(ex)
        for r in symbols:
            if venues and str(r.get("Exchange", "")).upper() not in venues:
                continue  # e.g. US: NYSE / NASDAQ only, never OTC
            # r["Country"] is the country of the *exchange* (AMD on Xetra → Germany), never the issuer's
            # domicile: it is not used, PEA eligibility falls back to the ISIN prefix (US → not eligible).
            isin = str(r.get("Isin") or "").strip().upper()
            if len(isin) != 12:
                continue
            typ = str(r.get("Type", "")).lower()
            if typ in ("etc", "etn") and isin not in (crypto_isins or set()):
                continue
            if typ == "etf" and not include_etf and isin not in (crypto_isins or set()):
                continue
            out.append(
                SymbolRow(
                    isin=isin,
                    name=str(r.get("Name", "")),
                    code=str(r.get("Code", "")),
                    exchange=ex,
                    currency=str(r.get("Currency") or "EUR"),
                    kind=(
                        "crypto_etp"
                        if crypto_isins and isin in crypto_isins
                        else "etf"
                        if str(r.get("Type", "")).lower() == "etf"
                        else "stock"
                    ),
                )
            )
    return out


def backfill_new_listings(provider, rows: list[SymbolRow], config, today: date, max_items: int = 1000) -> int:  # type: ignore[no-untyped-def]
    """History for listings that could enter the universe but lack the ``min_history_sessions`` it
    requires (a market just enabled, a new listing): without it they would stay excluded for a year.
    Only stocks whose market cap is known and above the market's floor are fetched (quota-friendly);
    a quota exhaustion stops cleanly with what was stored."""
    from sqlalchemy import func, select

    from app.db.models import PriceEod
    from app.services.market_data import upsert_eod
    from app.services.universe import _filters_for, filters_from

    base = filters_from(config)
    need = int(base.min_history_sessions)
    with db_session() as s:
        have = dict(s.execute(select(PriceEod.isin, func.count()).group_by(PriceEod.isin)).all())
    todo = [
        r
        for r in rows
        if r.kind == "stock"
        and r.market_cap_eur is not None
        and r.market_cap_eur >= _filters_for(base, config, r.exchange).min_market_cap_eur
        and have.get(r.isin, 0) < need
    ][:max_items]
    if not todo:
        return 0
    start = today - timedelta(days=int(need * 1.6))  # ≈ 400 calendar days for 250 sessions
    log.info("historique manquant : %d valeurs à charger depuis le %s", len(todo), start)
    n = 0
    with db_session() as s:
        for k, r in enumerate(todo, 1):
            ticker = f"{r.code}.{r.exchange}"
            try:
                n += upsert_eod(s, provider.fetch_eod([ticker], start, today), {ticker: r.isin})
            except QuotaExceeded as exc:
                log.warning("quota EODHD atteint après %d valeurs : %s — suite au prochain passage", k - 1, exc)
                break
            except ProviderError as exc:
                log.info("historique %s ignoré : %s", ticker, str(exc)[:120])
                continue
            if k % 50 == 0:
                s.commit()
                log.info("historique : %d/%d valeurs", k, len(todo))
    return len(todo)


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


def enrich_with_screener(  # type: ignore[no-untyped-def]
    rows: list[SymbolRow], config, fx_to_eur: dict[str, float | None] | None = None
) -> list[SymbolRow]:
    """Market cap, sector, earnings date from the delayed screener (facts only, no prices used)."""
    tv_cfg = config.params.data.tradingview_screener or {}
    if not tv_cfg or not config.params.data.screener:
        return rows
    from app.data.providers.tradingview_screener import MARKETS_BY_EODHD, TV_EXCHANGE, TradingViewScreener

    markets = sorted({MARKETS_BY_EODHD[r.exchange] for r in rows if r.exchange in MARKETS_BY_EODHD})
    try:
        tv = TradingViewScreener(
            min_interval_seconds=int(tv_cfg.get("min_interval_seconds", 60)),
            max_requests_per_scan=int(tv_cfg.get("max_requests_per_scan", 6)),
            limit_per_request=int(tv_cfg.get("limit_per_request", 1500)),
        )
        scan = []
        for i in range(0, len(markets), tv.max_requests):  # all markets, in polite batches (docs/04 §4.7)
            if i:
                time.sleep(tv.min_interval)
            scan += tv.scan(markets[i : i + tv.max_requests])
    except Exception as exc:  # noqa: BLE001
        log.warning("screener indisponible, univers sans capitalisation/secteur : %s", exc)
        for r in rows:
            r.extra["screener_error"] = exc.__class__.__name__
        return rows
    fx = fx_to_eur or {}
    by_key = {r.ticker: r for r in scan}  # EXCHANGE:SYMBOL — never merged across venues (M21)
    for r in rows:
        market = MARKETS_BY_EODHD.get(r.exchange)
        sr = None
        for prefix in (TV_EXCHANGE.get(market or "", "") or "").split("|") if market else []:
            sr = by_key.get(f"{prefix}:{r.code.replace('_', '.')}") or by_key.get(f"{prefix}:{r.code}")
            if sr is not None:
                break
        if sr is None:
            continue
        # market_cap_basic is expressed in the fundamental currency when the screener gives it, else
        # in the quote currency (to be confirmed in qualification A0.7). Non-euro caps are converted
        # at the stored ECB reference rate; no rate → left unset ("capitalisation inconnue").
        cap_ccy = str(sr.extra.get("fundamental_currency_code") or sr.extra.get("currency") or r.currency)
        rate = 1.0 if cap_ccy == "EUR" else fx.get(cap_ccy)
        r.market_cap_eur = sr.market_cap * rate if sr.market_cap is not None and rate else None
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
    markets = list(u.get("markets_p0", [])) + list(u.get("markets_p1", [])) + list(u.get("markets_p2", []))
    markets += list(u.get("markets_ex_pea", [])) + list(u.get("markets_us", []))  # docs/22 §22.6 M2, M3
    for ex in markets:
        mic = ctx.calendars.eodhd_to_mic.get(ex)
        if mic in ctx.calendars.venues:
            exchanges.append(ex)
        else:
            skipped.append(ex)  # ADR-005 M7: a market without a calendar is not followed yet
    if skipped:
        log.warning("places sans calendrier, non chargées : %s (encoder market_calendars.yaml)", ", ".join(skipped))
    errors: dict[str, str] = {}
    crypto = set(((ctx.config.raw.get("crypto") or {}).get("etp_isins")) or [])
    rows = symbol_rows_from_eodhd(
        provider, exchanges, errors, include_etf=bool(u.get("include_etf", False)), crypto_isins=crypto
    )
    with db_session() as s:
        from app.services.market_data import fx_to_eur

        fx = {
            c: fx_to_eur(s, c) for c in {r.currency for r in rows} | {"SEK", "DKK", "NOK", "PLN", "GBP", "CHF", "USD"}
        }
    rows = enrich_with_screener(rows, ctx.config, fx)
    if errors:
        _log_exchange_hints(provider, errors)
    if not rows:
        raise ProviderError("aucun symbole chargé : " + " ; ".join(f"{k}: {v}" for k, v in errors.items()))
    fetched = backfill_new_listings(
        provider, rows, ctx.config, date.today(), int(u.get("history_backfill_max_per_run", 1000))
    )
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
    top3 = sorted(rep.reasons.items(), key=lambda kv: -kv[1])[:3]
    ctx.note = (
        f"{len(exchanges) - len(errors)} place(s), {rep.total} titres, {rep.included} retenus"
        + (f", historique chargé pour {fetched}" if fetched else "")
        + (" ; exclusions : " + ", ".join(f"{k} {v}" for k, v in top3) if top3 else "")
    )
    return rep.included
