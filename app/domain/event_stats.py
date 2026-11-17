"""Lot S3 (step 1 of the user's plan, 15/11/2026) — statistics of what followed a family of events. Pure.

The excess return of a value over the median of its venue is read in the source's own direction: a « buy » letter is
right when the value then beats its venue, a « sell » when it lags. Events without a direction (a filing, a radar
label) are read as they are. The figures are descriptive: no threshold here decides anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt
from statistics import mean, median, pstdev


@dataclass(frozen=True)
class GroupStats:
    n: int
    mean: float | None  # mean excess, signed by the source's direction
    median: float | None
    hit_rate: float | None  # share of events where the signed excess is positive
    t_stat: float | None  # mean / (sd / √n), descriptive
    sd: float | None = None  # standard deviation of the signed excess (for the contrasts between groups)

    def as_dict(self) -> dict[str, float | int | None]:
        return {"n": self.n, "moyenne": self.mean, "mediane": self.median, "reussite": self.hit_rate,
                "t": self.t_stat}  # fmt: skip


def total_return(c0: float, f0: float | None, c1: float, f1: float | None) -> float:
    """The return between two closes, dividends included where the stored adjustment factors allow (16/11/2026).

    ``f = adj_close / close``. On raw closes an ex-dividend date reads as a loss (the first measure counted it so);
    over a window ``f1 / f0`` adds it back. A factor loaded before a later dividend was known stays at 1 (the daily
    load), so the correction falls short there rather than overshooting; without a factor at either end the raw
    return is kept. A jump of the factor (split, rights issue) is not corrected here: ``broken`` voids the window."""
    if f0 is not None and f1 is not None and f0 > 0 and f1 > 0:
        return (c1 * f1) / (c0 * f0) - 1
    return c1 / c0 - 1


def signed(excess: float, direction: int | None) -> float:
    """The excess in the source's direction: × −1 for a « sell », as is for a « buy » or no direction."""
    return -excess if direction == -1 else excess


def group_stats(values: Sequence[float]) -> GroupStats:
    """n, mean, median, hit rate and t of signed excess returns (None where n is too small to say)."""
    v = [float(x) for x in values]
    n = len(v)
    if n == 0:
        return GroupStats(0, None, None, None, None)
    m = mean(v)
    sd = pstdev(v) if n > 1 else 0.0
    t = m / (sd / sqrt(n)) if n > 1 and sd > 0 else None
    return GroupStats(n, m, median(v), sum(1 for x in v if x > 0) / n, t, sd if n > 1 else None)


@dataclass(frozen=True)
class Contrast:
    n_up: int
    n_down: int
    diff: float | None  # raw excess after the « up » events minus after the « down » ones
    t_stat: float | None  # Welch, descriptive


def _pooled(groups: Sequence[GroupStats], sign: int) -> tuple[int, float | None, float | None]:
    """n, raw mean (``sign`` undoes the signing of a « down » group) and its standard error, over several groups."""
    gs = [(g.n, g.mean, g.sd) for g in groups if g.n > 0 and g.mean is not None]
    n = sum(k for k, _, _ in gs)
    if n == 0:
        return 0, None, None
    m = sum(k * sign * float(mu or 0.0) for k, mu, _ in gs) / n
    sds = [(k, sd) for k, _, sd in gs if sd is not None]
    if len(sds) < len(gs):
        return n, m, None
    se2 = sum((k / n) ** 2 * (float(sd) ** 2 / k) for k, sd in sds)
    return n, m, sqrt(se2)


def contrast(up: Sequence[GroupStats], down: Sequence[GroupStats]) -> Contrast:
    """After the « up » events of one source (a buy, a positive surprise) minus after its « down » events (a sale, a
    negative surprise), raw excess over the venue (16/11/2026). Both share whatever drifts the population (the
    companies that file, the venue's median): the difference is free of it, where each signed mean is not."""
    nu, mu, su = _pooled(up, 1)
    nd, md, sdn = _pooled(down, -1)
    if mu is None or md is None:
        return Contrast(nu, nd, None, None)
    diff = mu - md
    t = diff / sqrt(su**2 + sdn**2) if su is not None and sdn is not None and (su or sdn) else None
    return Contrast(nu, nd, diff, t)


def venue_median(returns: Sequence[float], min_n: int = 10) -> float | None:
    """The median return of a venue's values over the same sessions (None under ``min_n`` values)."""
    r = list(returns)
    return median(r) if len(r) >= min_n else None


def broken(closes: Sequence[float], adj_factors: Sequence[float | None], jump: float = 0.2, ratio: float = 4.0) -> bool:
    """A change of scale between two sessions inside the window: the adjustment factor ``adj_close / close`` moves
    by more than ``jump`` (split, reverse split, rights issue) or a close is ``ratio`` × or 1/``ratio`` of the one
    before (a unit change or a bad tick the factor does not show) — the window measures nothing then."""
    prev_f = None
    for k, c in enumerate(closes):
        if c <= 0:
            return True
        if k and (c / closes[k - 1] >= ratio or c / closes[k - 1] <= 1 / ratio):
            return True
        f = adj_factors[k] if k < len(adj_factors) else None
        if f and prev_f and abs(f / prev_f - 1) > jump:
            return True
        prev_f = f or prev_f
    return False


def difference(event: GroupStats, control: GroupStats) -> Contrast:
    """Signed mean after the events minus signed mean over their control windows (the same values, the same horizon,
    away from any event of the source), Welch t — the reading fixed on 16/11/2026 before any control was measured."""
    if event.mean is None or control.mean is None:
        return Contrast(event.n, control.n, None, None)
    diff = event.mean - control.mean
    if event.sd is None or control.sd is None or event.n < 2 or control.n < 2:
        return Contrast(event.n, control.n, diff, None)
    se = sqrt(event.sd**2 / event.n + control.sd**2 / control.n)
    return Contrast(event.n, control.n, diff, diff / se if se > 0 else None)


def trend_flags(closes: Sequence[float], window: int = 50) -> list[bool | None]:
    """For each close, whether it is above the mean of the last ``window`` closes (itself included) — None while
    fewer closes exist. The index's trend as context of an event (17/11/2026); read the day before the event."""
    out: list[bool | None] = []
    total = 0.0
    for k, c in enumerate(closes):
        total += c
        if k >= window:
            total -= closes[k - window]
        out.append(c > total / window if k >= window - 1 else None)
    return out
