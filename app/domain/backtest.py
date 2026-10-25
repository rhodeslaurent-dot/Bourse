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
