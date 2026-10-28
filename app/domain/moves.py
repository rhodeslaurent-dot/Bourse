"""Study of the rises the tool missed (docs/34, user request of 28/10/2026). Pure functions.

Two complementary measures, never one without the other (selection on the outcome):
- **recall**: the rises of more than ``min_rise`` that stand out of the daily noise (zigzag with a volatility
  threshold, labelled afterwards — a pivot is only known once the reversal happened, so the episodes are a
  label, never an input); for each one, what the tool would have said at the start;
- **precision**: on ordinary days taken without looking at what followed, the share of days that led to an
  exploitable rise (triple barrier from the next open: up barrier before the stop, within ``horizon`` sessions),
  as a base rate per volatility class, and the lift of each feature decile or tool stage over that base rate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.indicators import Bar


@dataclass(frozen=True)
class MoveParams:
    min_rise: float = 0.05  # cumulative close-to-close rise of an episode
    sigma_k: float = 2.0  # and ≥ sigma_k · σ_daily · √sessions (out of the noise of a random walk)
    reversal_min: float = 0.03  # zigzag reversal threshold: max(reversal_min, reversal_atr · ATR14 / close)
    reversal_atr: float = 1.5
    sigma_sessions: int = 20  # σ of the daily log returns before the start
    horizon: int = 10  # triple barrier: sessions after the entry
    stop_atr: float = 2.0  # triple barrier: stop at entry − stop_atr · ATR14
    jump_share: float = 0.5  # an episode whose largest session makes ≥ half of the rise is a « saut »


@dataclass(frozen=True)
class Episode:
    start: int  # index of the swing low (close)
    peak: int  # index of the swing high (close)
    rise: float  # close[peak] / close[start] − 1
    sigma: float  # daily σ before the start
    max_session_share: float  # largest close-to-close log gain inside the episode / total log rise
    confirmed: bool  # False when the data ends before the reversal that confirms the peak

    @property
    def sessions(self) -> int:
        return self.peak - self.start

    @property
    def shape(self) -> str:
        return "saut" if self.max_session_share >= 0.5 else "progressive"


def daily_sigma(closes: Sequence[float], end: int, n: int) -> float | None:
    """σ of the log returns of the ``n`` sessions ending at ``end`` (excluded)."""
    lo = end - n
    if lo < 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(lo, end) if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < n // 2:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / max(1, len(rets) - 1))


def atr_series(bars: Sequence[Bar], n: int = 14) -> list[float | None]:
    """Wilder-free simple ATR (mean true range of the ``n`` previous sessions, the session included)."""
    tr: list[float] = []
    out: list[float | None] = []
    for i, b in enumerate(bars):
        prev = bars[i - 1].close if i else b.close
        tr.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
        out.append(sum(tr[-n:]) / n if len(tr) >= n else None)
    return out


def zigzag(closes: Sequence[float], thresholds: Sequence[float]) -> list[tuple[int, str]]:
    """Pivots ``(index, 'L'|'H')`` of the closes: a pivot is confirmed when the price reverses by the threshold
    of the pivot session. The last extreme is appended unconfirmed (flag it with ``confirmed=False``)."""
    n = len(closes)
    if n < 2:
        return []
    piv: list[tuple[int, str]] = []
    trend, hi, lo = 0, 0, 0
    for i in range(1, n):
        c = closes[i]
        if trend >= 0 and c > closes[hi]:
            hi = i
        if trend <= 0 and c < closes[lo]:
            lo = i
        if trend == 0:
            if c >= closes[lo] * (1 + thresholds[lo]):
                piv.append((lo, "L"))
                trend, hi = 1, i
            elif c <= closes[hi] * (1 - thresholds[hi]):
                piv.append((hi, "H"))
                trend, lo = -1, i
        elif trend == 1 and c <= closes[hi] * (1 - thresholds[hi]):
            piv.append((hi, "H"))
            trend, lo = -1, i
        elif trend == -1 and c >= closes[lo] * (1 + thresholds[lo]):
            piv.append((lo, "L"))
            trend, hi = 1, i
    if trend == 1:
        piv.append((hi, "H?"))
    elif trend == -1:
        piv.append((lo, "L?"))
    return piv


def find_episodes(bars: Sequence[Bar], p: MoveParams) -> list[Episode]:
    """Upswings L → H of the zigzag whose rise is ≥ ``min_rise`` and ≥ ``sigma_k`` · σ · √sessions."""
    closes = [b.close for b in bars]
    atrs = atr_series(bars)
    thr = [
        max(p.reversal_min, p.reversal_atr * (a / c)) if a is not None and c > 0 else p.reversal_min
        for a, c in zip(atrs, closes, strict=True)
    ]
    piv = zigzag(closes, thr)
    out: list[Episode] = []
    for (i0, k0), (i1, k1) in zip(piv, piv[1:], strict=False):
        if k0 != "L" or not k1.startswith("H") or i1 <= i0 or closes[i0] <= 0:
            continue
        sigma = daily_sigma(closes, i0 + 1, p.sigma_sessions)
        if sigma is None:
            continue
        rise = closes[i1] / closes[i0] - 1
        if rise < p.min_rise or rise < p.sigma_k * sigma * math.sqrt(i1 - i0):
            continue
        total = math.log(closes[i1] / closes[i0])
        best = max(math.log(closes[j] / closes[j - 1]) for j in range(i0 + 1, i1 + 1))
        out.append(Episode(i0, i1, rise, sigma, best / total if total > 0 else 1.0, not k1.endswith("?")))
    return out


def barrier_label(bars: Sequence[Bar], k: int, atr: float | None, sigma: float | None, p: MoveParams) -> int | None:
    """Triple barrier seen from the close of session ``k``: entry at the next open; 1 when the high reaches
    ``max(min_rise, sigma_k · σ · √horizon)`` above the entry before the low reaches the stop
    (entry − ``stop_atr`` · ATR) within ``horizon`` sessions; 0 otherwise (a session touching both counts as a
    loss — prudence); None when the data ends first or the inputs are missing."""
    if atr is None or sigma is None or k + 1 >= len(bars):
        return None
    entry = bars[k + 1].open
    if entry <= 0:
        return None
    up = entry * (1 + max(p.min_rise, p.sigma_k * sigma * math.sqrt(p.horizon)))
    down = entry - p.stop_atr * atr
    if k + p.horizon >= len(bars):
        return None
    for b in bars[k + 1 : k + 1 + p.horizon]:
        if b.low <= down:
            return 0
        if b.high >= up:
            return 1
    return 0


def size_bucket(rise: float) -> str:
    return "5–10 %" if rise < 0.10 else "10–20 %" if rise < 0.20 else "> 20 %"


def vol_bucket(sigma: float | None) -> str:
    """Daily σ class: the base rate of a +5 % rise depends on it (a small volatile value makes it by noise)."""
    if sigma is None:
        return "inconnue"
    return "calme (< 1,5 %)" if sigma < 0.015 else "moyenne (1,5–3 %)" if sigma < 0.03 else "forte (> 3 %)"


@dataclass(frozen=True)
class DecileRow:
    lo: float
    hi: float
    n: int
    rate: float  # share of exploitable rises in the decile
    lift: float  # rate / base rate


def decile_lift(values: Sequence[float | None], labels: Sequence[int | None], bins: int = 10) -> list[DecileRow]:
    """Lift of each quantile bin of a feature over the base rate, on the rows where both are known. Bins share
    edges when the feature has ties (e.g. booleans) — they are merged."""
    pairs = sorted((float(v), int(y)) for v, y in zip(values, labels, strict=True) if v is not None and y is not None)
    pairs = [(v, y) for v, y in pairs if not math.isnan(v)]
    if len(pairs) < bins * 20:
        return []
    base = sum(y for _, y in pairs) / len(pairs)
    if base <= 0:
        return []
    rows: list[DecileRow] = []
    n = len(pairs)
    i = 0
    for b in range(1, bins + 1):
        j = round(n * b / bins)
        while j < n and pairs[j][0] == pairs[j - 1][0]:
            j += 1  # never split a tie between two bins
        if j <= i:
            continue
        chunk = pairs[i:j]
        rate = sum(y for _, y in chunk) / len(chunk)
        rows.append(DecileRow(chunk[0][0], chunk[-1][0], len(chunk), rate, rate / base))
        i = j
        if i >= n:
            break
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % interval of a proportion (Wilson): the sample size needed is read from its width."""
    if n == 0:
        return 0.0, 1.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)
