"""``daily_regime`` (07:30, also called by the evening pipeline): docs/06 §6.2 components from our stored
data, **per market** (docs/32): each venue is judged on its own index and on the breadth of its own
values; the European row (STOXX 600, whole universe) sets the risk level of the day and is the fallback of
a venue without index. Missing components are reported, never guessed."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Instrument, MarketRegimeRow, PriceEod
from app.db.session import db_session
from app.domain.indicators import breadth_above_ma, distribution_days, sma, vol_percentile
from app.domain.regime import RegimeInputs, RegimeParams, compute_regime
from app.scheduler.runner import JobContext
from app.services.indices import EUROPE, index_specs, max_age_days
from app.services.market_data import load_bars

log = logging.getLogger("bourse.jobs.daily_regime")

INDEX_ISINS = {"cac40": "FR0003500008", "stoxx600": "EU0009658202"}  # default, overridden by params.regime.index_isins


def regime_params_from(config) -> RegimeParams:  # type: ignore[no-untyped-def]
    r = config.params.regime or {}
    return RegimeParams(
        r["red_min_components"],
        r["breadth_green"],
        r["breadth_orange"],
        r["distribution_days_orange"],
        r["distribution_days_red"],
        r["vol_pct_orange"],
        r["vol_pct_red"],
    )


def _above(bars) -> bool | None:  # type: ignore[no-untyped-def]
    return (bars[-1].close > sma([b.close for b in bars], 50)) if len(bars) >= 50 else None  # type: ignore[operator]


def compute_market(
    s: Session, config, day: date, market: str, index_id: str, europe_id: str, mics: set[str] | None
) -> MarketRegimeRow:  # type: ignore[no-untyped-def]
    """One market's regime: its index vs MM50 and the European index vs MM50, the breadth of its own
    universe values (``mics``; None = the whole universe), distribution days and volatility of its index."""
    r = config.params.regime or {}
    sessions = int(r.get("distribution_days_sessions", 25))
    drop = float(r.get("distribution_day_drop", 0.002))
    idx = load_bars(s, index_id, end=day)
    eu = idx if index_id == europe_id else load_bars(s, europe_id, end=day)
    closes = {}
    q = select(Instrument).where(Instrument.in_universe.is_(True))
    if mics is not None:
        q = q.where(Instrument.mic.in_(sorted(mics)))
    for inst in s.scalars(q):
        bars = load_bars(s, inst.isin, end=day, limit=60)
        if len(bars) >= 50:
            closes[inst.isin] = [b.close for b in bars]
    min_n = int(r.get("breadth_min_values", 10))
    breadth = breadth_above_ma(closes, 50) if len(closes) >= min_n else None  # too few values: no breadth
    dist = distribution_days(idx, sessions, drop) if len(idx) >= sessions + 1 else None
    vol = vol_percentile(idx) if len(idx) >= 30 else None
    regime = compute_regime(RegimeInputs(_above(idx), _above(eu), breadth, dist, vol), regime_params_from(config))
    return MarketRegimeRow(
        day=day,
        ts=datetime.now(UTC),
        market=market,
        cac_vs_mm50=_above(idx),  # the market's own index (column kept from the CAC-only era)
        stoxx_vs_mm50=_above(eu),
        breadth_mm50=breadth,
        distribution_days=dist,
        vol_pct=vol,
        regime=regime.light.value,
        details={
            "components": {k: (v.value if v else None) for k, v in regime.components.items()},
            "missing": regime.missing,
            "index": index_id,
            "index_sessions": len(idx),
            "breadth_n": len(closes),
        },
    )


def compute_and_store(ctx: JobContext) -> str:
    """Every market with an index, then Europe; returns the European light (the risk level of the day)."""
    specs = index_specs(ctx.config)
    europe = next(x for x in specs if x.market == EUROPE)
    by_market: dict[str, str] = {}
    with db_session() as s:
        followed = {m for m in s.scalars(select(Instrument.mic).where(Instrument.in_universe.is_(True)).distinct())}
        oldest = ctx.run_date - timedelta(days=max_age_days(ctx.config))
        for spec in specs:
            if spec.market != EUROPE and spec.market not in followed:
                continue  # a venue without any value in the universe: nothing to gate
            last = s.scalar(
                select(func.max(PriceEod.day)).where(PriceEod.isin == spec.id, PriceEod.day <= ctx.run_date)
            )
            if spec.market != EUROPE and (last is None or last < oldest):
                # docs/32: an absent or stale index gives no regime (the venue follows Europe, said so on the
                # fiche) — never a « today » regime computed on old closes (ISEQ stopped in 2022, 26/10/2026)
                log.warning("régime %s non calculé : indice %s absent ou périmé", spec.market, spec.id)
                continue
            mics = None if spec.market == EUROPE else {spec.market}
            row = compute_market(s, ctx.config, ctx.run_date, spec.market, spec.id, europe.id, mics)
            s.add(row)
            by_market[spec.market] = row.regime
    log.info("régimes du %s : %s", ctx.run_date, ", ".join(f"{k} {v}" for k, v in sorted(by_market.items())))
    return by_market.get(EUROPE, "orange")


def run(ctx: JobContext) -> int:
    light = compute_and_store(ctx)
    log.info("régime européen %s pour %s", light, ctx.run_date)
    return 1
