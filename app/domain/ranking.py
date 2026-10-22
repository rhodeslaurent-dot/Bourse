"""Ranking of admitted opportunities (docs/22 §22.4, decision Q5 of 22/10/2026). Pure.

No maximum number of ideas: everything admitted is ranked, the ranking does the filtering.
Order: expected net R (when the comparable history is sufficient), then net reward/risk, then net
gain at the target in %. Unknown values rank after known ones, never estimated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankItem:
    key: str
    net_reward_risk: float | None
    net_gain_pct: float | None
    hit_rate: float | None = None  # shrunk comparable hit rate (docs/18), None if insufficient
    expected_r: float | None = None  # shrunk expected R of comparable proposals, when already known


def expected_net_r(hit_rate: float | None, net_reward_risk: float | None) -> float | None:
    """p × reward − (1 − p) × 1R, in R units of the net loss at the stop."""
    if hit_rate is None or net_reward_risk is None:
        return None
    return hit_rate * net_reward_risk - (1 - hit_rate)


def _desc(v: float | None) -> tuple[int, float]:
    return (1, 0.0) if v is None else (0, -v)


def rank(items: list[RankItem]) -> list[tuple[int, RankItem, float | None]]:
    """Returns (rank from 1, item, expected net R)."""
    scored = [
        (it, it.expected_r if it.expected_r is not None else expected_net_r(it.hit_rate, it.net_reward_risk))
        for it in items
    ]
    scored.sort(key=lambda x: (_desc(x[1]), _desc(x[0].net_reward_risk), _desc(x[0].net_gain_pct), x[0].key))
    return [(i + 1, it, e) for i, (it, e) in enumerate(scored)]
