"""Feedback statistics and the bounded adjustment of new proposals (docs/18 §18.5, docs/12 §12.5).

Pure functions on final outcomes. No parameter is ever optimised here: thresholds come from
``params.yaml › learning`` and the result is displayed, sorted on, or (docs/12 §12.5.3) used to
downgrade a BUY to WATCH. Sizes, stops and risk rules are never touched.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

Z90 = 1.645


@dataclass(frozen=True)
class Sample:
    """One final outcome with its grouping keys (taken from the proposal's snapshot)."""

    proposal_id: int
    created_at: datetime
    final_at: datetime | None  # when the outcome became final (exit day), for no-look-ahead track records
    r: float | None  # R net of costs when counted, else gross; None without stop
    ret_pct: float | None
    keys: dict[str, str | None] = field(default_factory=dict)
    values: dict[str, float | None] = field(default_factory=dict)  # numeric facts for threshold tables


@dataclass(frozen=True)
class GroupStat:
    key: str
    n: int  # samples with an R
    wins: int
    win_rate: float | None
    mean_r: float | None
    median_r: float | None
    ci_low: float | None
    ci_high: float | None
    n_ret: int  # samples with a return (all)
    mean_ret_pct: float | None
    reliability: str  # insuffisant | indicatif | suffisant


def reliability(n: int, min_display: int, min_judge: int) -> str:
    if n >= min_judge:
        return "suffisant"
    if n >= min_display:
        return "indicatif"
    return "insuffisant"


def mean_ci(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    """Mean and 90 % normal-approximation interval (None bounds under 2 values)."""
    if not values:
        return None, None, None
    m = statistics.fmean(values)
    if len(values) < 2:
        return m, None, None
    half = Z90 * statistics.stdev(values) / math.sqrt(len(values))
    return m, m - half, m + half


def stat_of(key: str, samples: Iterable[Sample], min_display: int, min_judge: int) -> GroupStat:
    ss = list(samples)
    rs = [x.r for x in ss if x.r is not None]
    rets = [x.ret_pct for x in ss if x.ret_pct is not None]
    m, lo, hi = mean_ci(rs)
    wins = sum(1 for r in rs if r > 0)
    return GroupStat(
        key=key,
        n=len(rs),
        wins=wins,
        win_rate=wins / len(rs) if rs else None,
        mean_r=m,
        median_r=statistics.median(rs) if rs else None,
        ci_low=lo,
        ci_high=hi,
        n_ret=len(rets),
        mean_ret_pct=statistics.fmean(rets) if rets else None,
        reliability=reliability(len(rs) or len(rets), min_display, min_judge),
    )


def group_stats(
    samples: Iterable[Sample], by: str, min_display: int, min_judge: int, missing: str = "inconnu"
) -> list[GroupStat]:
    groups: dict[str, list[Sample]] = {}
    for x in samples:
        groups.setdefault(x.keys.get(by) or missing, []).append(x)
    out = [stat_of(k, v, min_display, min_judge) for k, v in groups.items()]
    return sorted(out, key=lambda g: (-(g.n or g.n_ret), g.key))


def bucket_label(value: float | None, edges: Sequence[float], fmt: Callable[[float], str] = str) -> str | None:
    """'< e0', 'e0 – e1', …, '≥ eN' (edges ascending)."""
    if value is None:
        return None
    if value < edges[0]:
        return f"< {fmt(edges[0])}"
    for a, b in zip(edges, edges[1:], strict=False):
        if a <= value < b:
            return f"{fmt(a)} – {fmt(b)}"
    return f"≥ {fmt(edges[-1])}"


def threshold_table(
    samples: Iterable[Sample],
    field_name: str,
    edges: Sequence[float],
    min_display: int,
    min_judge: int,
    fmt: Callable[[float], str] = str,
) -> list[GroupStat]:
    """Outcome by bucket of a numeric fact (candidate thresholds for the AI review: the bucket
    edges are the only values a parameter suggestion may propose)."""
    groups: dict[str, list[Sample]] = {}
    order = [f"< {fmt(edges[0])}"] + [f"{fmt(a)} – {fmt(b)}" for a, b in zip(edges, edges[1:], strict=False)]
    order.append(f"≥ {fmt(edges[-1])}")
    for x in samples:
        lab = bucket_label(x.values.get(field_name), edges, fmt)
        if lab is not None:
            groups.setdefault(lab, []).append(x)
    return [stat_of(k, groups[k], min_display, min_judge) for k in order if k in groups]


def shrunk_r(mean_r: float | None, n: int, k: float) -> float | None:
    return None if mean_r is None or n <= 0 else n / (n + k) * mean_r


@dataclass(frozen=True)
class TrackRecord:
    scope: str  # "détecteur + source" | "détecteur" | "aucun"
    n: int
    mean_r: float | None
    shrunk_r: float | None
    verdict: str  # favorable | neutre | defavorable | insuffisant

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "n": self.n,
            "mean_r": None if self.mean_r is None else round(self.mean_r, 3),
            "shrunk_r": None if self.shrunk_r is None else round(self.shrunk_r, 3),
            "verdict": self.verdict,
        }


def classify(n: int, shrunk: float | None, min_n: int, favorable: float, unfavorable: float) -> str:
    if shrunk is None or n < min_n:
        return "insuffisant"
    if shrunk >= favorable:
        return "favorable"
    if shrunk <= unfavorable:
        return "defavorable"
    return "neutre"


def track_record(
    history: Iterable[Sample],
    at: datetime,
    detector: str | None,
    source: str | None,
    *,
    k: float,
    min_n: int,
    favorable: float,
    unfavorable: float,
) -> TrackRecord:
    """Comparable past cases known *at* ``at``: created before and final before it (no look-ahead).
    Same detector and source first; detector only when that scope is too small."""
    known = [
        x for x in history if x.r is not None and x.created_at < at and x.final_at is not None and x.final_at <= at
    ]
    scopes = [
        (
            "détecteur + source",
            [x for x in known if x.keys.get("detector") == detector and x.keys.get("source") == source],
        ),
        ("détecteur", [x for x in known if x.keys.get("detector") == detector]),
    ]
    for name, ss in scopes:
        if len(ss) >= min_n or name == "détecteur":
            if not ss:
                return TrackRecord("aucun", 0, None, None, "insuffisant")
            rs = [x.r for x in ss if x.r is not None]
            m = statistics.fmean(rs)
            sh = shrunk_r(m, len(rs), k)
            return TrackRecord(name, len(rs), m, sh, classify(len(rs), sh, min_n, favorable, unfavorable))
    return TrackRecord("aucun", 0, None, None, "insuffisant")  # pragma: no cover


def should_demote(action: str, track: TrackRecord, min_signals: int, enabled: bool) -> bool:
    """docs/12 §12.5.3: a BUY whose comparable history is unfavourable on ≥ ``min_signals`` cases
    is issued as WATCH."""
    return enabled and action == "BUY" and track.verdict == "defavorable" and track.n >= min_signals


VERDICT_ORDER = {"favorable": 0, "neutre": 1, "insuffisant": 1, "defavorable": 2}
