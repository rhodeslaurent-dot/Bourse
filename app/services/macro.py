"""Free macro data (docs/22 §22.7, lot 2.2): FX reference rates, dashboard series, economic
calendar, and the conjunctural risk inputs of docs/07 §7.9.4.

Every stored point keeps its source and status; a value outside its plausible range is rejected,
never stored; a fallback source is used only when the previous one returns nothing, and the
source of each point stays visible (no silent switch). Nothing is estimated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MacroEvent, MacroPoint, MarketRegimeRow
from app.domain.conjuncture import (
    BrentParams,
    ConjunctureParams,
    Threshold,
    conjuncture,
    market_gate,
    srd_mode,
)
from app.domain.indicators import percentile_rank
from app.services.market_data import load_bars, upsert_fx

log = logging.getLogger("bourse.macro")

LABELS = {
    "brent": "Brent ($)",
    "vix": "VIX",
    "gold": "Or ($)",
    "eurusd": "EUR/USD",
    "us10y": "Taux 10 ans US (%)",
    "fr10y": "OAT 10 ans (%)",
    "de10y": "Bund 10 ans (%)",
    "sp500": "S&P 500",
    "nikkei": "Nikkei 225",
    "msci_world": "MSCI World (proxy coté)",
}

# (day, close, market timestamp) from one provider for one symbol
SeriesFetcher = Callable[[str, date, date], list[tuple[date, float]]]


# --- FX (ECB) ----------------------------------------------------------------------------------


def store_fx(s: Session, points: list[Any]) -> int:
    n = 0
    for p in points:
        upsert_fx(s, f"EUR{p.currency}", p.day, p.rate, "ecb")
        n += 1
    s.flush()
    return n


# --- dashboard series -------------------------------------------------------------------------


def default_fetchers(config: Any) -> dict[str, SeriesFetcher]:
    """Provider name → fetcher. Imports are lazy so tests inject their own fetchers."""

    def yf(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
        from app.data.providers.yfinance_eod import YFinanceProvider

        return [(b.day, b.close) for b in YFinanceProvider().fetch_eod([symbol], start, end + timedelta(days=1))]

    def eodhd(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
        import os

        if not os.environ.get("EODHD_API_TOKEN"):
            return []
        from app.data.providers.eodhd import eodhd_from

        return [(b.day, b.close) for b in eodhd_from(config).fetch_eod([symbol], start, end)]

    def ecb(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
        from app.data.providers import ecb_fx

        period = "90d" if (end - start).days <= 90 else "hist"
        return [(p.day, p.rate) for p in ecb_fx.fetch(period) if p.currency == symbol and start <= p.day <= end]

    def tradingview(symbol: str, start: date, end: date) -> list[tuple[date, float]]:
        """``symbol`` = ``market/TICKER`` (e.g. ``cfd/TVC:FR10Y``). Only the last value exists: it is
        dated today after 18:00 Paris (the European session is over), else the previous weekday."""
        from app.data.providers.tradingview_screener import latest_values
        from app.domain.timeutil import PARIS

        market, _, ticker = symbol.partition("/")
        vals = latest_values([ticker], market or "cfd")
        if ticker not in vals:
            return []
        now = datetime.now(PARIS)
        d = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return [(d, vals[ticker])] if start <= d <= end + timedelta(days=1) else []

    return {"yfinance": yf, "eodhd": eodhd, "ecb": ecb, "tradingview": tradingview}


@dataclass
class SnapshotReport:
    stored: int = 0
    sources: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def note(self) -> str:
        parts = []
        fallback = [f"{k} : {v}" for k, v in self.sources.items() if v.endswith("(secours)")]
        if fallback:
            parts.append("sources de secours — " + ", ".join(fallback))
        if self.missing:
            parts.append("indisponibles : " + ", ".join(self.missing))
        if self.rejected:
            parts.append("hors plage : " + ", ".join(self.rejected))
        return " · ".join(parts)


def macro_settings(config: Any) -> dict[str, Any]:
    return dict(getattr(config.params.data, "macro", None) or {})


def snapshot(s: Session, config: Any, today: date, fetchers: dict[str, SeriesFetcher] | None = None) -> SnapshotReport:
    cfg = macro_settings(config)
    fetchers = fetchers or default_fetchers(config)
    rep = SnapshotReport()
    now = datetime.now(UTC)
    for indicator, spec in (cfg.get("series") or {}).items():
        sources = spec["sources"] if isinstance(spec, dict) else spec
        lo, hi = (spec.get("valid_range") or [None, None]) if isinstance(spec, dict) else (None, None)
        last = s.scalar(select(MacroPoint.day).where(MacroPoint.indicator == indicator).order_by(MacroPoint.day.desc()))
        start = (last - timedelta(days=7)) if last else today - timedelta(days=int(cfg.get("history_days", 400)))
        for rank, src in enumerate(sources):
            fetch = fetchers.get(src["provider"])
            if fetch is None:
                continue
            try:
                points = fetch(str(src["symbol"]), start, today)
            except Exception as exc:  # noqa: BLE001 — a free source may fail; the next one is tried
                log.warning("macro %s via %s indisponible : %s", indicator, src["provider"], exc)
                points = []
            scale = float(src.get("scale", 1.0))
            kept = []
            for d, v in points:
                val = v * scale
                if (lo is not None and val < lo) or (hi is not None and val > hi):
                    rep.rejected.append(f"{indicator} {d} {val:g} ({src['provider']})")
                    continue
                kept.append((d, val))
            if not kept:
                continue
            label = f"{src['provider']}:{src['symbol']}" + (" (secours)" if rank else "")
            rep.sources[indicator] = label
            for d, val in kept:
                row = s.scalar(select(MacroPoint).where(MacroPoint.indicator == indicator, MacroPoint.day == d))
                if row is None:
                    row = MacroPoint(indicator=indicator, day=d)
                    s.add(row)
                    rep.stored += 1
                row.value, row.source, row.symbol = val, str(src["provider"]), str(src["symbol"])
                row.market_timestamp = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
                row.received_at, row.data_status = now, "eod"
            break
        else:
            rep.missing.append(indicator)
    s.flush()
    return rep


def series(s: Session, indicator: str, end: date | None = None, limit: int = 300) -> list[MacroPoint]:
    q = select(MacroPoint).where(MacroPoint.indicator == indicator)
    if end:
        q = q.where(MacroPoint.day <= end)
    rows = list(s.scalars(q.order_by(MacroPoint.day.desc()).limit(limit)))
    return rows[::-1]


def latest(s: Session, end: date | None = None) -> dict[str, MacroPoint]:
    out: dict[str, MacroPoint] = {}
    for ind in LABELS:
        pts = series(s, ind, end, limit=1)
        if pts:
            out[ind] = pts[-1]
    return out


# --- economic calendar -------------------------------------------------------------------------


def store_events(s: Session, events: list[Any], currencies: list[str], impacts: list[str]) -> int:
    now = datetime.now(UTC)
    n = 0
    for e in events:
        if e.currency not in currencies or e.impact not in impacts:
            continue
        row = s.scalar(
            select(MacroEvent).where(
                MacroEvent.ts == e.ts, MacroEvent.currency == e.currency, MacroEvent.title == e.title
            )
        )
        if row is None:
            row = MacroEvent(ts=e.ts, currency=e.currency, title=e.title)
            s.add(row)
            n += 1
        row.impact, row.forecast, row.previous = e.impact, e.forecast, e.previous
        row.source, row.received_at = "forexfactory", now
    s.flush()
    return n


def upcoming_events(s: Session, now: datetime, days: int = 7, impacts: tuple[str, ...] = ("High",)) -> list[MacroEvent]:
    q = (
        select(MacroEvent)
        .where(MacroEvent.ts >= now, MacroEvent.ts <= now + timedelta(days=days), MacroEvent.impact.in_(impacts))
        .order_by(MacroEvent.ts)
    )
    return list(s.scalars(q))


# --- conjunctural risk and gate (docs/07 §7.9.4) ---------------------------------------------------


def conjuncture_params(config: Any) -> ConjunctureParams | None:
    c = getattr(config.params, "conjuncture", None)
    if not c:
        return None
    th = {
        "vix_pct_1y": Threshold(**c["vix_pct_1y"]),
        "oat_bund_spread_bp": Threshold(**c["oat_bund_spread_bp"]),
        "us10y_pct": Threshold(**c["us10y_pct"]),
        "index_5d_change_pct": Threshold(**c["index_5d_change_pct"], higher_is_worse=False),
    }
    return ConjunctureParams(th, int(c["high_min_components"]))


def conjuncture_inputs(s: Session, config: Any, day: date) -> dict[str, float | None]:
    vix = series(s, "vix", day, limit=260)
    vix_pct = percentile_rank(vix[-1].value, [p.value for p in vix]) / 100 if len(vix) >= 120 else None
    fr, de = series(s, "fr10y", day, 1), series(s, "de10y", day, 1)
    spread = (fr[-1].value - de[-1].value) * 100 if fr and de and fr[-1].day == de[-1].day else None
    us = series(s, "us10y", day, 1)
    gate = getattr(config.params, "market_gate", None) or {}
    idx = load_bars(s, str(gate.get("index_isin", "FR0003500008")), end=day, limit=6)
    chg = idx[-1].close / idx[0].close - 1 if len(idx) == 6 else None
    return {
        "vix_pct_1y": vix_pct,
        "oat_bund_spread_bp": spread,
        "us10y_pct": us[-1].value if us else None,
        "index_5d_change_pct": chg,
    }


def brent_srd_mode(s: Session, config: Any, day: date) -> str | None:
    """Deterministic replay of the hysteresis over the last 120 closes (no hidden state)."""
    b = (getattr(config.params, "conjuncture", None) or {}).get("brent")
    pts = series(s, "brent", day, limit=120)
    if not b or not pts:
        return None
    p = BrentParams(b["srd_restrict_above"], b["srd_normal_below"], b["stable_sessions"])
    mode = "normal"
    closes: list[float] = []
    for pt in pts:
        closes.append(pt.value)
        mode = srd_mode(closes, mode, p)
    return mode


@dataclass
class MarketContext:
    day: date
    regime: str | None
    level: str | None
    components: dict[str, str]
    explanations: list[str]
    gate: str | None
    gate_reason: str
    srd_mode: str | None
    values: dict[str, MacroPoint]
    inputs: dict[str, float | None]


def market_context(s: Session, config: Any, day: date, published_ok: bool | None = None) -> MarketContext:
    """Conjunctural level + gate state. ``published_ok`` (option C conditions read from the letters)
    is wired in lot 2.3; until then it is unknown, which counts as not met (restricted gate)."""
    reg = s.scalar(select(MarketRegimeRow).where(MarketRegimeRow.day <= day).order_by(MarketRegimeRow.id.desc()))
    regime = reg.regime if reg else None
    inputs = conjuncture_inputs(s, config, day)
    params = conjuncture_params(config)
    level, comps, expl, gate, why = None, {}, [], None, "paramètres conjuncture absents"
    if params is not None:
        c = conjuncture(regime, inputs, params)
        level, comps, expl = c.level, c.components, c.explanations
        g = market_gate(published_ok, c.level, regime)
        gate, why = g.state, g.reason
    return MarketContext(
        day, regime, level, comps, expl, gate, why, brent_srd_mode(s, config, day), latest(s, day), inputs
    )
