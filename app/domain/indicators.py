"""Indicator definitions (docs/06 annexe) — every one tested in ``tests/unit/test_indicators.py``.

Inputs are plain lists (oldest first) so that the domain stays free of I/O. Units: ATR14 in
quote currency, ADR20 in fraction (0.02 = 2 %), RVOL dimensionless, RS rank 0–100.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: int


def sma(values: list[float], n: int) -> float | None:
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def sma_series(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def true_range(bars: list[Bar]) -> list[float]:
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            tr.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return tr


def atr(bars: list[Bar], n: int = 14) -> float | None:
    """Wilder's ATR: first value = simple mean of the first n TR, then smoothed."""
    if len(bars) < n + 1:
        return None
    tr = true_range(bars)
    a = sum(tr[1 : n + 1]) / n
    for x in tr[n + 1 :]:
        a = (a * (n - 1) + x) / n
    return a


def adr(bars: list[Bar], n: int = 20) -> float | None:
    """Average daily range in fraction: mean of (high/low − 1) over n sessions."""
    if len(bars) < n:
        return None
    return sum(b.high / b.low - 1 for b in bars[-n:] if b.low > 0) / n


def rvol_day(bars: list[Bar], n: int = 20) -> float | None:
    """Volume of the last bar / mean volume of the previous n bars (excluding the last)."""
    if len(bars) < n + 1:
        return None
    ref = sum(b.volume for b in bars[-n - 1 : -1]) / n
    return bars[-1].volume / ref if ref else None


def returns(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-1 - sessions] == 0:
        return None
    return closes[-1] / closes[-1 - sessions] - 1


def rs_score(closes: list[float]) -> float | None:
    """Minervini: 0.4·r12m + 0.2·r6m + 0.2·r3m + 0.2·r1m (sessions: 250 / 125 / 63 / 21)."""
    r12, r6, r3, r1 = returns(closes, 250), returns(closes, 125), returns(closes, 63), returns(closes, 21)
    if None in (r12, r6, r3, r1):
        return None
    return 0.4 * r12 + 0.2 * r6 + 0.2 * r3 + 0.2 * r1  # type: ignore[operator]


def percentile_rank(value: float, population: list[float]) -> float:
    """Rank 0–100 = share of the population strictly below ``value`` (ties count half)."""
    if not population:
        return 0.0
    below = sum(1 for x in population if x < value)
    ties = sum(1 for x in population if x == value)
    return 100.0 * (below + 0.5 * max(ties - 1, 0)) / max(len(population) - 1, 1) if len(population) > 1 else 100.0


def ti65(closes: list[float]) -> float | None:
    a, b = sma(closes, 7), sma(closes, 65)
    return None if a is None or b is None or b == 0 else a / b


def pivot(bars: list[Bar], lookback: int = 60) -> float | None:
    """Highest high of the previous ``lookback`` sessions (excluding the current bar)."""
    if len(bars) < lookback + 1:
        return None
    return max(b.high for b in bars[-lookback - 1 : -1])


def high_52w(bars: list[Bar]) -> float | None:
    return max(b.high for b in bars[-250:]) if bars else None


def consolidation(bars: list[Bar], n: int = 20) -> float | None:
    """(max(high, n) − min(low, n)) / min(low, n) over the n sessions *before* the current bar."""
    if len(bars) < n + 1:
        return None
    window = bars[-n - 1 : -1]
    hi, lo = max(b.high for b in window), min(b.low for b in window)
    return (hi - lo) / lo if lo else None


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes[-n - 1 : -1], closes[-n:], strict=False):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains) / n, sum(losses) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + ag / al)


def gap_open(open_price: float, prev_close: float) -> float:
    return open_price / prev_close - 1 if prev_close else 0.0


def distribution_days(index_bars: list[Bar], sessions: int = 25, drop: float = 0.002) -> int:
    """Sessions with close ≤ −0.2 % and volume > previous session, over the last ``sessions``."""
    n = 0
    window = index_bars[-sessions - 1 :]
    for prev, cur in zip(window, window[1:], strict=False):
        if prev.close and cur.close / prev.close - 1 <= -drop and cur.volume > prev.volume:
            n += 1
    return n


def breadth_above_ma(closes_by_symbol: dict[str, list[float]], n: int = 50) -> float | None:
    above = total = 0
    for closes in closes_by_symbol.values():
        m = sma(closes, n)
        if m is None:
            continue
        total += 1
        above += closes[-1] > m
    return above / total if total else None


def vol_percentile(bars: list[Bar], lookback: int = 250) -> float | None:
    """Percentile (1 y) of today's ATR14/close among the daily ATR14/close series."""
    if len(bars) < 30:
        return None
    series: list[float] = []
    for i in range(20, len(bars) + 1):
        a = atr(bars[max(0, i - 40) : i], 14)
        if a is not None and bars[i - 1].close:
            series.append(a / bars[i - 1].close)
    if not series:
        return None
    window = series[-lookback:]
    return percentile_rank(window[-1], window)


@dataclass(frozen=True)
class DailyFeatures:
    atr14: float | None
    adr20: float | None
    mm10: float | None
    mm20: float | None
    mm50: float | None
    mm200: float | None
    rvol: float | None
    rs_1m: float | None
    rs_3m: float | None
    rs_6m: float | None
    rs_12m: float | None
    rs_score: float | None
    pivot_60: float | None
    high_52w: float | None
    consolidation_20: float | None
    ti65: float | None
    rsi14: float | None
    sessions: int

    @property
    def complete(self) -> bool:
        return self.sessions >= 250 and self.rs_score is not None


def compute_daily_features(bars: list[Bar]) -> DailyFeatures:
    closes = [b.close for b in bars]
    return DailyFeatures(
        atr14=atr(bars, 14),
        adr20=adr(bars, 20),
        mm10=sma(closes, 10),
        mm20=sma(closes, 20),
        mm50=sma(closes, 50),
        mm200=sma(closes, 200),
        rvol=rvol_day(bars, 20),
        rs_1m=returns(closes, 21),
        rs_3m=returns(closes, 63),
        rs_6m=returns(closes, 125),
        rs_12m=returns(closes, 250),
        rs_score=rs_score(closes),
        pivot_60=pivot(bars, 60),
        high_52w=high_52w(bars),
        consolidation_20=consolidation(bars, 20),
        ti65=ti65(closes),
        rsi14=rsi(closes, 14),
        sessions=len(bars),
    )


def is_nan(x: float | None) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))
