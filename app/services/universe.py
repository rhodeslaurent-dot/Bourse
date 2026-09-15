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


@dataclass
class RefreshReport:
    total: int = 0
    included: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    snapshot_id: int | None = None
    watchlist: int = 0


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
    )


def refresh_universe(
    s: Session, config: LoadedConfig, calendars: MarketCalendars, rows: list[SymbolRow], day: date
) -> RefreshReport:
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
        inst.ticker_local = r.code
        inst.ticker_eodhd = f"{r.code}.{r.exchange}"
        inst.ticker_tv = r.tv_ticker or inst.ticker_tv
        inst.exchange, inst.mic, inst.currency = r.exchange, mic, r.currency or "EUR"
        inst.country_of_domicile = r.country or inst.country_of_domicile
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
        dec = universe_decision(facts, filt)
        pea = pea_regulatory_eligibility(r.isin, inst.country_of_domicile, countries, inst.pea_available_boursobank)
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
