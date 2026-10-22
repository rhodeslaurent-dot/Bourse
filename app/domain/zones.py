"""Support / resistance zones detected from daily bars (docs/06 §6.7). Pure.

Pivot highs and lows (a bar whose high/low is the extreme of ``pivot_window`` bars on each side)
are grouped when closer than ``merge_atr_mult × ATR``; a zone needs ``min_touches`` pivots.
Unfilled gaps are zones too. A zone published by a letter takes precedence (handled by callers).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.indicators import Bar
from app.domain.stops import Zone


@dataclass(frozen=True)
class DetectedZone:
    low: float
    high: float
    touches: int
    kind: str  # pivots | gap

    @property
    def zone(self) -> Zone:
        return Zone(self.low, self.high)


def pivot_levels(bars: list[Bar], window: int) -> list[float]:
    levels: list[float] = []
    for i in range(window, len(bars) - window):
        span = bars[i - window : i + window + 1]
        if bars[i].high == max(b.high for b in span):
            levels.append(bars[i].high)
        if bars[i].low == min(b.low for b in span):
            levels.append(bars[i].low)
    return levels


def cluster(levels: list[float], tolerance: float, min_touches: int) -> list[DetectedZone]:
    out: list[DetectedZone] = []
    group: list[float] = []
    for lv in sorted(levels):
        if group and lv - group[0] > tolerance:
            if len(group) >= min_touches:
                out.append(DetectedZone(group[0], group[-1], len(group), "pivots"))
            group = []
        group.append(lv)
    if len(group) >= min_touches:
        out.append(DetectedZone(group[0], group[-1], len(group), "pivots"))
    return out


def unfilled_gaps(bars: list[Bar]) -> list[DetectedZone]:
    """Up-gaps (low > previous high) never revisited afterwards, and the symmetric down-gaps."""
    out: list[DetectedZone] = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        after = bars[i + 1 :]
        if cur.low > prev.high and all(b.low > prev.high for b in after):
            out.append(DetectedZone(prev.high, cur.low, 1, "gap"))
        elif cur.high < prev.low and all(b.high < prev.low for b in after):
            out.append(DetectedZone(cur.high, prev.low, 1, "gap"))
    return out


def detect_zones(
    bars: list[Bar], atr: float, pivot_window: int, merge_atr_mult: float, min_touches: int
) -> list[DetectedZone]:
    zones = cluster(pivot_levels(bars, pivot_window), merge_atr_mult * atr, min_touches) + unfilled_gaps(bars)
    return sorted(zones, key=lambda z: z.low)


def supports_below(price: float, zones: list[DetectedZone]) -> list[DetectedZone]:
    """Zones whose low is under the price, nearest first."""
    return sorted((z for z in zones if z.low < price), key=lambda z: price - z.low)


def resistances_above(price: float, zones: list[DetectedZone]) -> list[DetectedZone]:
    """Zones entirely above the price, nearest first."""
    return sorted((z for z in zones if z.low > price), key=lambda z: z.low - price)


def exploitable_targets(entry: float, zones: list[DetectedZone], extra_levels: list[float]) -> list[float]:
    """Target candidates above the entry, nearest first: the low bound of each resistance zone (the
    price meets the zone there) plus extra structural levels (20/60-session high, 52-week high,
    published target). Never an R multiple."""
    levels = [z.low for z in resistances_above(entry, zones)] + [lv for lv in extra_levels if lv > entry]
    return sorted(set(levels))
