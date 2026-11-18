"""18/11/2026 — user's remark: « NVIDIA, −25 % over the last months, very good results last night and very strong
news: it is not normal that it is not in the watchlist ». The momentum watchlist keeps the strongest values only, so
a beaten-down value that has just published results the market welcomed never entered it.

The market's reaction to a results publication, from stored closes only (never an opinion on the figures): from the
last close before the publication to the close of the first session after it, with that session's volume against the
average of the 20 sessions before. Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class DayBar:
    day: date
    close: float
    volume: float


@dataclass(frozen=True)
class Reaction:
    ref_day: date  # last session closed before the publication
    day: date  # first session after it
    move: float  # close of ``day`` / close of ``ref_day`` − 1
    rel_volume: float | None  # volume of ``day`` / average of the 20 sessions up to ``ref_day``


def results_reaction(published_local: datetime, close_time: time, bars: list[DayBar]) -> Reaction | None:
    """``published_local`` in the venue's time zone. A publication before the close counts for that session (before
    the open: the whole session; during it: its close), after the close for the next one. None while the first
    session after the publication is not closed yet, or without a close before it."""
    pub_day, after_close = published_local.date(), published_local.time() >= close_time
    bars = sorted(bars, key=lambda b: b.day)
    before = [b for b in bars if b.day < pub_day or (b.day == pub_day and after_close)]
    after = [b for b in bars if b.day > pub_day or (b.day == pub_day and not after_close)]
    if not before or not after or before[-1].close <= 0:
        return None
    ref, react = before[-1], after[0]
    window = [b.volume for b in before[-20:] if b.volume]
    avg = sum(window) / len(window) if window else 0.0
    return Reaction(ref.day, react.day, react.close / ref.close - 1, (react.volume / avg) if avg > 0 else None)


def welcomed(r: Reaction | None, min_move: float, min_rel_volume: float) -> bool:
    """A clear, traded rise: at least ``min_move`` and a volume of at least ``min_rel_volume`` times the average."""
    return r is not None and r.move >= min_move and r.rel_volume is not None and r.rel_volume >= min_rel_volume
