"""``daily_regime`` (07:30, also called by the evening pipeline): docs/06 §6.2 components from our
stored data. Missing components are reported, never guessed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Instrument, MarketRegimeRow
from app.db.session import db_session
from app.domain.indicators import breadth_above_ma, distribution_days, sma, vol_percentile
from app.domain.regime import RegimeInputs, RegimeParams, compute_regime
from app.scheduler.runner import JobContext
from app.services.market_data import load_bars

log = logging.getLogger("bourse.jobs.daily_regime")

INDEX_ISINS = {"cac40": "FR0003500008", "stoxx600": "EU0009658202"}  # index pseudo-instruments (EODHD .INDX)


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


def compute_and_store(ctx: JobContext) -> str:
    with db_session() as s:
        cac = load_bars(s, INDEX_ISINS["cac40"], end=ctx.run_date)
        stoxx = load_bars(s, INDEX_ISINS["stoxx600"], end=ctx.run_date)
        cac_above = (cac[-1].close > sma([b.close for b in cac], 50)) if len(cac) >= 50 else None  # type: ignore[operator]
        stoxx_above = (stoxx[-1].close > sma([b.close for b in stoxx], 50)) if len(stoxx) >= 50 else None  # type: ignore[operator]
        closes = {}
        for inst in s.scalars(select(Instrument).where(Instrument.in_universe.is_(True))):
            bars = load_bars(s, inst.isin, end=ctx.run_date, limit=60)
            if len(bars) >= 50:
                closes[inst.isin] = [b.close for b in bars]
        breadth = breadth_above_ma(closes, 50) if closes else None
        dist = distribution_days(cac, 25) if len(cac) >= 26 else None
        vol = vol_percentile(stoxx) if len(stoxx) >= 30 else None
        regime = compute_regime(
            RegimeInputs(cac_above, stoxx_above, breadth, dist, vol), regime_params_from(ctx.config)
        )
        s.add(
            MarketRegimeRow(
                day=ctx.run_date,
                ts=datetime.now(UTC),
                cac_vs_mm50=cac_above,
                stoxx_vs_mm50=stoxx_above,
                breadth_mm50=breadth,
                distribution_days=dist,
                vol_pct=vol,
                regime=regime.light.value,
                details={
                    "components": {k: (v.value if v else None) for k, v in regime.components.items()},
                    "missing": regime.missing,
                },
            )
        )
        return regime.light.value


def run(ctx: JobContext) -> int:
    light = compute_and_store(ctx)
    log.info("régime %s pour %s", light, ctx.run_date)
    return 1
