"""Backtest primitives (docs/27): simulation of one limit-order trade on daily bars, conservative by
construction, and robust summary statistics. Pure functions — no I/O, no LLM.

Conservative choices (docs/27 §27.3):
- a buy limit fills only if the session **trades through** it (low strictly below the limit), or at the
  open when the open is already below the limit (gap down); never on a mere touch;
- on the fill session, a stop hit is assumed to happen **after** the fill (worst case);
- when stop and target are both inside one session, the **stop** is assumed first;
- a gap through the stop exits at the open, slipped; the target exits at the limit (or the open if the
  session opens above it);
- a trade still open after ``max_hold`` sessions exits at the close (time stop).
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain.indicators import Bar


@dataclass(frozen=True)
class TradeResult:
    entry_index: int  # index in the bars passed (0 = first session after the signal)
    exit_index: int
    entry: float
    exit: float
    reason: str  # stop | gap_stop | target | time
    gross_return: float  # exit / entry − 1
    risk_fraction: float  # (entry − slipped stop) / entry
    mfe_r: float  # best excursion while held, in R
    mae_r: float  # worst excursion while held, in R (negative)

    @property
    def gross_r(self) -> float:
        return self.gross_return / self.risk_fraction

    def net_r(self, round_trip_cost: float) -> float:
        """R after the round-trip costs (fees, taxes on transactions, change), as a fraction of notional."""
        return (self.gross_return - round_trip_cost) / self.risk_fraction


def simulate_trade(
    bars: list[Bar],
    limit: float,
    stop: float,
    target: float,
    slippage_pct: float,
    entry_sessions: int,
    max_hold: int,
) -> TradeResult | None:
    """``bars`` = the sessions **after** the signal session. None when the limit never fills, or when the
    session opens below the stop before any fill (setup invalidated, no entry)."""
    stop_exec = stop * (1 - slippage_pct)
    if not (stop_exec < limit < target):
        return None
    fill_i, entry = -1, 0.0
    for i, b in enumerate(bars[:entry_sessions]):
        if b.open <= stop:
            return None  # invalidated before the order could fill
        if b.open <= limit:
            fill_i, entry = i, b.open
            break
        if b.low < limit:
            fill_i, entry = i, limit
            break
    if fill_i < 0:
        return None
    risk = (entry - stop_exec) / entry
    if risk <= 0:
        return None
    hi, lo = entry, entry
    for j in range(fill_i, min(len(bars), fill_i + max_hold)):
        b = bars[j]
        if j > fill_i and b.open <= stop:
            px = b.open * (1 - slippage_pct)
            return _result(fill_i, j, entry, px, "gap_stop", risk, max(hi, b.high), min(lo, b.low))
        hi, lo = max(hi, b.high), min(lo, b.low)
        if b.low <= stop:
            return _result(fill_i, j, entry, stop_exec, "stop", risk, hi, lo)
        if j > fill_i and b.open >= target:
            return _result(fill_i, j, entry, b.open, "target", risk, hi, lo)
        if b.high >= target:
            return _result(fill_i, j, entry, target, "target", risk, hi, lo)
    if fill_i + max_hold > len(bars):
        return None  # not enough history to know the outcome: never counted as a win or a loss
    last = fill_i + max_hold - 1
    return _result(fill_i, last, entry, bars[last].close, "time", risk, hi, lo)


def _result(i: int, j: int, entry: float, px: float, reason: str, risk: float, hi: float, lo: float) -> TradeResult:
    return TradeResult(i, j, entry, px, reason, px / entry - 1, risk, (hi / entry - 1) / risk, (lo / entry - 1) / risk)


@dataclass(frozen=True)
class Summary:
    n: int
    mean_r: float | None
    median_r: float | None
    hit_rate: float | None
    t_stat: float | None
    ci_low: float | None  # bootstrap 95 % interval of the mean R
    ci_high: float | None
    profit_factor: float | None
    avg_hold: float | None
    significant: bool  # t ≥ 3 (Harvey, Liu & Zhu) — the bar for a « discovered » rule

    def as_dict(self) -> dict[str, float | int | bool | None]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def summarize(rs: list[float], holds: list[int] | None = None, seed: int = 7, draws: int = 1000) -> Summary:
    """Mean / median R, hit rate, t-statistic, bootstrap interval (fixed seed: reproducible) and profit factor."""
    n = len(rs)
    if n == 0:
        return Summary(0, None, None, None, None, None, None, None, None, False)
    mean = statistics.fmean(rs)
    sd = statistics.stdev(rs) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else None
    rng = random.Random(seed)
    boots = sorted(statistics.fmean(rng.choices(rs, k=n)) for _ in range(draws)) if n > 1 else [mean]
    lo, hi = boots[int(0.025 * (len(boots) - 1))], boots[int(0.975 * (len(boots) - 1))]
    gains, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return Summary(
        n,
        mean,
        statistics.median(rs),
        sum(r > 0 for r in rs) / n,
        t,
        lo,
        hi,
        gains / losses if losses > 0 else None,
        statistics.fmean(holds) if holds else None,
        bool(t is not None and t >= 3.0),
    )


# --- portfolio replay (docs/27 §27.7, contre-expertise du 25/10/2026) ---------------------------------


@dataclass(frozen=True)
class ReplayTrade:
    entry_day: date
    exit_day: date
    r_net: float  # net R at full size


@dataclass(frozen=True)
class BreakerRule:
    loss_5_r: float = 4.0
    loss_21_r: float = 8.0
    peak_reduce_r: float = 10.0
    peak_block_r: float = 12.0
    reduce_multiplier: float = 0.75


@dataclass(frozen=True)
class PortfolioResult:
    taken: int
    skipped_cap: int
    skipped_breaker: int
    total_r: float
    r_per_year: float | None
    max_drawdown_r: float
    worst_21_sessions_r: float
    return_over_drawdown: float | None

    def as_dict(self) -> dict[str, float | int | None]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _change(days: list[date], vals: list[float], now: float, entry: date, sessions: int) -> float:
    """Realised R gained since ``sessions`` sessions before ``entry`` (calendar approximation 7/5)."""
    lim = entry - timedelta(days=sessions * 7 // 5)
    base = [c for d, c in zip(days, vals, strict=True) if d < lim]
    return now - (base[-1] if base else 0.0)


def portfolio_replay(
    trades: list[ReplayTrade], max_concurrent: int, breaker: BreakerRule | None = None
) -> PortfolioResult:
    """Chronological replay at 1 R per trade with at most ``max_concurrent`` open trades (the open-risk cap ÷
    the risk per trade) and, optionally, the rolling circuit breaker on the **realised** R curve (latent
    ignored: a simplification, stated). Trades are taken first come, first served (no look-ahead)."""
    ts = sorted(trades, key=lambda t: (t.entry_day, t.exit_day))
    open_: list[tuple[date, float]] = []  # (exit day, R at the size taken)
    realised: list[tuple[date, float]] = []  # closed trades (exit day, R)
    taken = cap = brk = 0

    def curve_until(d: date) -> list[tuple[date, float]]:
        return sorted((x for x in realised if x[0] < d), key=lambda x: x[0])

    def cum_by_day(pts: list[tuple[date, float]]) -> list[tuple[date, float]]:
        out: list[tuple[date, float]] = []
        c = 0.0
        for d, r in pts:
            c += r
            if out and out[-1][0] == d:
                out[-1] = (d, c)
            else:
                out.append((d, c))
        return out

    for t in ts:
        for x in [o for o in open_ if o[0] <= t.entry_day]:
            open_.remove(x)
            realised.append(x)
        if len(open_) >= max_concurrent:
            cap += 1
            continue
        size = 1.0
        if breaker is not None:
            cum = cum_by_day(curve_until(t.entry_day))  # only exits before the entry session are known
            vals = [c for _, c in cum]
            now = vals[-1] if vals else 0.0
            days = [d for d, _ in cum]

            # peak of the last 21 sessions = the curve level at the window start or any level reached since; a
            # 0 floor kept the replay blocked for ever once 12 R were lost (18 trades in 8 years, 26/10/2026)
            w0 = t.entry_day - timedelta(days=30)
            before = [c for d, c in zip(days, vals, strict=True) if d < w0]
            inside = [c for d, c in zip(days, vals, strict=True) if d >= w0]
            peak = max([before[-1] if before else 0.0, *inside])
            if (
                _change(days, vals, now, t.entry_day, 5) <= -breaker.loss_5_r
                or _change(days, vals, now, t.entry_day, 21) <= -breaker.loss_21_r
                or now - peak <= -breaker.peak_block_r
            ):
                brk += 1
                continue
            if now - peak <= -breaker.peak_reduce_r:
                size = breaker.reduce_multiplier
        open_.append((t.exit_day, t.r_net * size))
        taken += 1
    realised += open_
    cum = cum_by_day(sorted(realised, key=lambda x: x[0]))
    vals = [c for _, c in cum]
    peak, mdd = 0.0, 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    worst21 = 0.0
    for i, (d, v) in enumerate(cum):
        prior = [c for dd, c in cum[:i] if dd <= d - timedelta(days=30)]
        worst21 = min(worst21, v - (prior[-1] if prior else 0.0))
    total = vals[-1] if vals else 0.0
    years = ((cum[-1][0] - ts[0].entry_day).days / 365.25) if cum and ts else 0.0
    rpy = total / years if years > 0.5 else None
    return PortfolioResult(
        taken, cap, brk, total, rpy, mdd, worst21, (rpy / -mdd) if rpy is not None and mdd < 0 else None
    )
