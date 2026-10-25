"""Universe refresh (docs/05): referential from provider symbol lists + screener facts + our EOD
prices; PEA/SRD flags; liquidity filters; versioned snapshot with diff; momentum watchlist."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    Instrument,
    InstrumentAlias,
    PeaEligibilityRow,
    SrdEligibilityRow,
    UniverseMember,
    UniverseSnapshot,
    WatchlistRow,
)
from app.domain.calendar import MarketCalendars
from app.domain.universe import (
    InstrumentFacts,
    MomentumCandidate,
    SrdStatus,
    UniverseFilters,
    momentum_watchlist,
    pea_regulatory_eligibility,
    primary_exchange,
    srd_status_from_rules,
    universe_decision,
)
from app.services.market_data import adv_eur_20, fx_to_eur, latest_features, load_bars

log = logging.getLogger("bourse.universe")


@dataclass
class SymbolRow:
    """One provider symbol (EODHD ``exchange-symbol-list``) enriched with screener facts."""

    isin: str
    name: str
    code: str
    exchange: str  # EODHD code (PA, XETRA…)
    currency: str
    country: str | None = None
    market_cap_eur: float | None = None
    sector: str | None = None
    industry: str | None = None
    earnings_next_date: date | None = None
    tv_ticker: str | None = None
    extra: dict = field(default_factory=dict)
    kind: str = "stock"  # stock | etf


@dataclass
class RefreshReport:
    total: int = 0
    reasons: dict[str, int] = field(default_factory=dict)  # exclusion reason → count (diagnostics)
    included: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    snapshot_id: int | None = None
    watchlist: int = 0


def primary_listings(rows: list[SymbolRow], overrides: dict[str, str] | None = None) -> list[SymbolRow]:
    """One row per ISIN: its primary listing (``primary_exchange``), in the order of first appearance.
    Issuer-level facts the primary row lacks (market cap, sector, industry, next earnings) are taken from
    another listing of the same ISIN; the tickers and the currency always stay those of the primary one."""
    by: dict[str, list[SymbolRow]] = {}
    for r in rows:
        by.setdefault(r.isin, []).append(r)
    out = []
    for isin, group in by.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        ex = primary_exchange(isin, [r.exchange for r in group], overrides)
        main = next(r for r in group if r.exchange == ex)
        for other in group:
            if other is main:
                continue
            main.market_cap_eur = main.market_cap_eur if main.market_cap_eur is not None else other.market_cap_eur
            main.sector = main.sector or other.sector
            main.industry = main.industry or other.industry
            main.earnings_next_date = main.earnings_next_date or other.earnings_next_date
        main.extra.setdefault("autres_cotations", sorted(f"{r.code}.{r.exchange}" for r in group if r is not main))
        out.append(main)
    return out


def listing_overrides(config: LoadedConfig) -> dict[str, str]:
    """``universe.primary_listing_overrides``: ISIN → EODHD exchange code, when the home-exchange rule is
    wrong for an issuer (e.g. a Dutch holding whose primary listing is Paris)."""
    raw = (config.params.universe or {}).get("primary_listing_overrides") or {}
    return {str(k).upper(): str(v) for k, v in raw.items()}


def filters_from(config: LoadedConfig) -> UniverseFilters:
    u = config.params.universe or {}
    return UniverseFilters(
        u["min_adv_eur_cto"],
        u["min_adv_eur_pea"],
        u["min_market_cap_eur"],
        u["small_cap_threshold_eur"],
        u["min_price"],
        u["min_history_sessions"],
        list(u.get("blacklist", [])),
        float(u["min_adv_eur_etf"]) if u.get("min_adv_eur_etf") is not None else None,
    )


def _filters_for(base: UniverseFilters, config: LoadedConfig, exchange: str) -> UniverseFilters:
    """``universe.market_overrides.<EODHD code>`` (e.g. US: larger caps and liquidity)."""
    from dataclasses import replace

    ov = ((config.params.universe or {}).get("market_overrides") or {}).get(exchange) or {}
    return replace(base, **{k: v for k, v in ov.items() if k in base.__dataclass_fields__}) if ov else base


def refresh_universe(
    s: Session, config: LoadedConfig, calendars: MarketCalendars, rows: list[SymbolRow], day: date
) -> RefreshReport:
    rows = primary_listings(rows, listing_overrides(config))  # never a secondary listing over the primary one
    rep = RefreshReport(total=len(rows))
    filt = filters_from(config)
    countries = config.params.pea["eligible_countries"] if config.params.pea else []
    srd_raw = config.raw.get("srd", {})
    previous = {i.isin for i in s.scalars(select(Instrument).where(Instrument.in_universe.is_(True)))}
    included: list[tuple[str, dict]] = []
    now = datetime.now(UTC)
    for r in rows:
        mic = calendars.eodhd_to_mic.get(r.exchange, r.exchange)
        inst = s.get(Instrument, r.isin)
        if inst is None:
            inst = Instrument(isin=r.isin, name=r.name, mic=mic)
            s.add(inst)
        inst.name = r.name or inst.name
        if inst.exchange and inst.exchange != r.exchange:
            inst.ticker_tv = None  # the old venue's TradingView ticker (XETR:TOTB) never follows the new listing
        inst.ticker_local = r.code
        inst.ticker_eodhd = f"{r.code}.{r.exchange}"
        inst.ticker_tv = r.tv_ticker or inst.ticker_tv
        inst.exchange, inst.mic, inst.currency = r.exchange, mic, r.currency or "EUR"
        inst.country_of_domicile = r.country  # issuer domicile only (None from EODHD: its Country is the exchange's)
        inst.sector = r.sector or inst.sector
        inst.industry = r.industry or inst.industry
        if r.earnings_next_date:
            inst.earnings_next_date, inst.earnings_source = r.earnings_next_date, "tradingview_screener"
        fx = fx_to_eur(s, inst.currency, day)
        bars = load_bars(s, r.isin, end=day)
        price = bars[-1].close if bars else None
        adv = adv_eur_20(bars, fx) if fx is not None else None
        inst.market_cap_eur = r.market_cap_eur if r.market_cap_eur is not None else inst.market_cap_eur
        inst.adv_eur_20 = adv
        facts = InstrumentFacts(
            r.isin, mic, price * fx if (price is not None and fx) else None, inst.market_cap_eur, adv, len(bars)
        )
        inst.kind = r.kind
        dec = universe_decision(
            facts, _filters_for(filt, config, r.exchange), "etf" if r.kind == "crypto_etp" else r.kind
        )
        for reason in dec.reasons:
            rep.reasons[reason] = rep.reasons.get(reason, 0) + 1
        pea = pea_regulatory_eligibility(r.isin, inst.country_of_domicile, countries, inst.pea_available_boursobank)
        if r.kind == "crypto_etp":
            from app.domain.universe import PeaEligibility

            pea = PeaEligibility(False, 1.0, "crypto_etp", "ETP crypto : compte-titres seulement")
        elif r.kind == "etf":
            from app.domain.universe import PeaEligibility, etf_pea_status

            ok, conf, src = etf_pea_status(
                r.isin, r.name, list((config.params.universe or {}).get("etf_pea_isins") or [])
            )
            pea = PeaEligibility(ok, conf, src, "ETF : éligibilité selon la liste ou le nom (à confirmer)")
        if inst.pea_eligible != pea.eligible or inst.pea_source != pea.source:
            s.add(
                PeaEligibilityRow(
                    isin=r.isin,
                    eligible=pea.eligible,
                    confidence=pea.confidence,
                    source=pea.source,
                    reason=pea.reason,
                    checked_at=now,
                )
            )
        inst.pea_eligible, inst.pea_confidence, inst.pea_source = pea.eligible, pea.confidence, pea.source
        srd = (
            srd_status_from_rules(
                mic,
                inst.market_cap_eur,
                adv,
                srd_raw.get("eligibility_full", {}),
                srd_raw.get("eligibility_long_only", {}),
            )
            if srd_raw
            else SrdStatus.UNKNOWN
        )
        if not inst.srd_confirmed_by_user and inst.srd_status != srd.value:
            s.add(SrdEligibilityRow(isin=r.isin, status=srd.value, source="rules", checked_at=now))
            inst.srd_status = srd.value
        inst.in_universe, inst.universe_reasons, inst.small_cap, inst.active = (
            dec.included,
            dec.reasons,
            dec.small_cap,
            True,
        )
        inst.updated_at = now
        for alias in {r.name, r.code, inst.ticker_eodhd}:
            if (
                alias
                and s.scalar(
                    select(InstrumentAlias).where(InstrumentAlias.provider == "eodhd", InstrumentAlias.alias == alias)
                )
                is None
            ):
                s.add(InstrumentAlias(isin=r.isin, provider="eodhd", alias=alias))
        if dec.included:
            included.append(
                (r.isin, {"cto_ok": dec.cto_ok, "small_cap": dec.small_cap, "pea": pea.eligible, "srd": srd.value})
            )
    s.flush()
    current = {i for i, _ in included}
    rep.included = len(current)
    rep.added = sorted(current - previous)
    rep.removed = sorted(previous - current)
    feats = latest_features(s, list(current))
    snap = UniverseSnapshot(
        day=day,
        version=config.params.version,
        filters=json.loads(json.dumps(filt.__dict__)),
        count=len(current),
        sha256=hashlib.sha256(",".join(sorted(current)).encode()).hexdigest(),
        added=rep.added,
        removed=rep.removed,
    )
    s.add(snap)
    s.flush()
    for isin, flags in included:
        inst = s.get(Instrument, isin)
        f = feats.get(isin)
        s.add(
            UniverseMember(
                snapshot_id=snap.id,
                isin=isin,
                adv_eur=inst.adv_eur_20 if inst else None,
                market_cap=inst.market_cap_eur if inst else None,
                rs_rank=f.rs_rank if f else None,
                flags=flags,
            )
        )
    rep.snapshot_id = snap.id
    rep.watchlist = refresh_momentum_watchlist(s, config, current, feats)
    return rep


def refresh_momentum_watchlist(s: Session, config: LoadedConfig, universe: set[str], feats: dict) -> int:
    u = config.params.universe or {}
    cands = []
    for isin in universe:
        f = feats.get(isin)
        if f is None or f.rs_rank is None:
            continue
        bars = load_bars(s, isin, limit=1)
        if not bars:
            continue
        cands.append(MomentumCandidate(isin, f.rs_rank, bars[-1].close, f.mm50, f.mm200))
    wl = momentum_watchlist(cands, u.get("momentum_watchlist_rs_rank_min", 80), u.get("momentum_watchlist_max", 100))
    keep = {c.isin for c in wl}
    for row in s.scalars(select(WatchlistRow).where(WatchlistRow.reason == "momentum", WatchlistRow.active.is_(True))):
        if row.isin not in keep:
            row.active = False
        else:
            keep.discard(row.isin)
            row.rs_rank = next(c.rs_rank for c in wl if c.isin == row.isin)
    for c in wl:
        if c.isin in keep:
            s.add(
                WatchlistRow(
                    isin=c.isin,
                    reason="momentum",
                    source="rs_rank",
                    active=True,
                    rs_rank=c.rs_rank,
                    levels={"mm50": c.mm50, "mm200": c.mm200},
                )
            )
    s.flush()
    return len(wl)


def refresh_watchlist_daily(s: Session, config: LoadedConfig) -> tuple[int, date | None]:
    """docs/18 §18.2 — momentum watchlist from the current universe and the latest computed
    features. Returns (size, day of the features used); the caller flags an old day."""
    universe = {i.isin for i in s.scalars(select(Instrument).where(Instrument.in_universe.is_(True)))}
    feats = latest_features(s, sorted(universe)) if universe else {}
    days = [f.day for f in feats.values() if f.day is not None]
    if not days:  # nothing computed yet: keep the current list rather than emptying it
        return len(list(s.scalars(select(WatchlistRow.id).where(WatchlistRow.active.is_(True))))), None
    return refresh_momentum_watchlist(s, config, universe, feats), (max(days) if days else None)


def tradingview_export(s: Session) -> str:
    """One ``EXCHANGE:TICKER`` per line for the active watchlist (docs/09 §9.5)."""
    lines = []
    for row in s.scalars(
        select(WatchlistRow).where(WatchlistRow.active.is_(True)).order_by(WatchlistRow.rs_rank.desc())
    ):
        inst = s.get(Instrument, row.isin)
        if inst and inst.ticker_tv:
            lines.append(inst.ticker_tv)
        elif inst and inst.ticker_local:
            lines.append(f"{_tv_exchange(inst.mic)}:{inst.ticker_local}")
    return "\n".join(lines)


def _tv_exchange(mic: str) -> str:
    return {
        "XPAR": "EURONEXT",
        "XAMS": "EURONEXT",
        "XBRU": "EURONEXT",
        "XLIS": "EURONEXT",
        "XMIL": "MIL",
        "XETR": "XETR",
        "XMAD": "BME",
        "XSTO": "OMXSTO",
        "XCSE": "OMXCOP",
        "XHEL": "OMXHEX",
        "XOSL": "OSL",
    }.get(mic, mic)
