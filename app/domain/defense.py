"""Step 2, option B (docs/42) — what a defence of a held position (keep, raise the stop, lighten, exit) would have
given against « do nothing », from the session after the evening review. Pure functions, no I/O.

Per share, in fraction of the review close, gross (before fees and taxes):

- *do nothing*: the position keeps its current stop; an open at or below the stop exits at the open, a low at or below
  it exits at the slipped stop, otherwise the close of the ``horizon``-th session;
- *exit*: the next open; *lighten* by ``part``: that part at the next open, the rest as *do nothing*;
- *raise the stop*: *do nothing* with the higher of the two stops; *keep*: *do nothing* (gap 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.indicators import Bar

ACTIONS = ("conserver", "remonter_stop", "alleger", "sortir")


@dataclass(frozen=True)
class Defense:
    action: str  # one of ACTIONS
    stop: float | None = None  # with « remonter_stop »
    part: float | None = None  # with « alleger »


def hold_value(bars: list[Bar], stop: float | None, horizon: int, slippage_pct: float) -> float | None:
    """Value per share after ``horizon`` sessions with ``stop`` in place (None when the window is not over)."""
    if horizon <= 0 or len(bars) < horizon:
        return None
    for b in bars[:horizon]:
        if stop is not None and b.open <= stop:
            return b.open  # gap through the stop: filled at the open
        if stop is not None and b.low <= stop:
            return stop * (1 - slippage_pct)
    return bars[horizon - 1].close


def defense_value(bars: list[Bar], d: Defense, stop: float | None, horizon: int, slippage_pct: float) -> float | None:
    rest = hold_value(bars, stop, horizon, slippage_pct)
    if rest is None:
        return None
    if d.action == "sortir":
        return bars[0].open
    if d.action == "alleger":
        q = min(1.0, max(0.0, d.part or 0.0))
        return q * bars[0].open + (1 - q) * rest
    if d.action == "remonter_stop" and d.stop is not None:
        return hold_value(bars, d.stop if stop is None else max(d.stop, stop), horizon, slippage_pct)
    return rest


def gap_vs_nothing(bars: list[Bar], d: Defense, stop: float | None, ref_close: float, horizon: int,
                   slippage_pct: float) -> float | None:  # fmt: skip
    """(defence − do nothing) / review close, per share; None when the window is not over or the close unknown."""
    a = defense_value(bars, d, stop, horizon, slippage_pct)
    b = hold_value(bars, stop, horizon, slippage_pct)
    if a is None or b is None or not ref_close or ref_close <= 0:
        return None
    return (a - b) / ref_close


def python_defense(py: dict[str, object] | None) -> Defense:
    """Python's own review line (exits rules) read as a defence: VENDRE → exit, ALLÉGER → lighten, a proposed stop
    above the current one → raise the stop, otherwise keep."""
    py = py or {}
    v = py.get("verdict")
    if v == "VENDRE":
        return Defense("sortir")
    if v == "ALLÉGER":
        part = py.get("alleger_pct")
        return Defense("alleger", part=float(part) if isinstance(part, int | float) else 0.5)
    sp, st = py.get("stop_propose"), py.get("stop")
    if isinstance(sp, int | float) and (not isinstance(st, int | float) or sp > st):
        return Defense("remonter_stop", stop=float(sp))
    return Defense("conserver")
