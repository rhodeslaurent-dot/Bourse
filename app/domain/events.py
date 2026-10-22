"""Exposure to an announcement a stop does not cover (docs/07 §7.9.5, docs/22 R14). Pure.

Measured on the stock's own opening gaps at its last events of the same type: the loss at the
``quantile`` gap, opened from the maximum entry price, expressed in R. Exposure is low when that
loss stays ≤ ``max_gap_loss_r``. Fewer than ``min_history`` events → unknown (no exception).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EventParams:
    history_count: int
    min_history: int
    gap_quantile: float
    max_gap_loss_r: float


@dataclass(frozen=True)
class EventExposure:
    known: bool
    low: bool
    events_used: int
    gap_median: float | None
    gap_quantile: float | None
    loss_r: float | None
    text: str


def abs_gaps(prev_closes: list[float], opens: list[float]) -> list[float]:
    return [abs(o / c - 1) for c, o in zip(prev_closes, opens, strict=True) if c > 0]


def quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def event_exposure(gaps: list[float], p_max: float, stop_exec: float, p: EventParams) -> EventExposure:
    used = gaps[-p.history_count :]
    if len(used) < p.min_history or p_max <= stop_exec:
        return EventExposure(
            False, False, len(used), None, None, None, f"exposition à l'annonce inconnue ({len(used)} événement(s))"
        )
    med = quantile(used, 0.5)
    gq = quantile(used, p.gap_quantile)
    loss_r = gq * p_max / (p_max - stop_exec)
    low = loss_r <= p.max_gap_loss_r
    text = (
        f"exposition à l'annonce : {'faible' if low else 'forte'} (écart médian {med:.1%}, "
        f"{p.gap_quantile:.0%}e centile {gq:.1%} = {loss_r:.1f} R, {len(used)} événements)"
    )
    return EventExposure(True, low, len(used), med, gq, loss_r, text)
