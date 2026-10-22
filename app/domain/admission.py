"""Admission tests of the management rules (docs/07 §7.9.1, docs/22 R4, R5, R15). Pure.

- R4: the maximum entry price makes the reward/risk ratio equal to ``min_reward_risk`` on an
  *exploitable* target, net of round-trip fees: P_max = [(target + k × stop) ÷ (1 + k)] × (1 − fees).
  No exception: a price above P_max, even by 0.26 %, is refused.
- R5: P_max is a necessary condition, never an entry signal. A price that reached P_max by closing
  through the support (or giving back a breakout) is a degraded configuration.
- R15: a letter's setup is testable only with an explicit invalidation and a numeric target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Setup(StrEnum):
    PULLBACK = "repli_support"  # the price tests a support zone that must hold
    BREAKOUT = "franchissement"  # the price closed above an obstacle that must not be given back


class Status(StrEnum):
    ADMITTED = "admis"
    REFUSED = "refuse"
    DEGRADED = "configuration_degradee"
    NOT_TESTABLE = "non_testable"


def p_max_rr(target: float, stop: float, fee_fraction: float, min_reward_risk: float) -> float:
    """Price at which (target − c) / (c − stop) = k, reduced by the round-trip fees."""
    k = min_reward_risk
    return (target + k * stop) / (1 + k) * (1 - fee_fraction)


def gross_reward_risk(price: float, target: float, stop: float) -> float | None:
    """Ratio as the management rules state it: (target − price) ÷ (price − stop)."""
    if price <= stop:
        return None
    return (target - price) / (price - stop)


def structure_intact(setup: Setup, close: float, level_low: float) -> bool:
    """R5. ``level_low`` is the low bound of the support zone (pullback) or of the obstacle zone
    that was crossed (breakout). A close below it means support lost / breakout given back."""
    return close >= level_low


@dataclass(frozen=True)
class AdmissionInput:
    price: float  # last valid price (close for an EOD setup)
    target: float | None  # first exploitable target
    stop: float | None  # invalidation level (stop placement rule already applied)
    setup: Setup
    level_low: float | None  # support zone low (pullback) / crossed obstacle low (breakout)
    fee_fraction: float  # round-trip fees as a fraction (costs.round_trip_fee_fraction)
    min_reward_risk: float  # admission.min_reward_risk
    p_max_technical: float | None = None  # detector's own cap (e.g. pivot + 1 %), if any


@dataclass
class Admission:
    status: Status
    p_max: float | None
    p_max_rr: float | None
    gross_reward_risk: float | None
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == Status.ADMITTED


def admit(inp: AdmissionInput) -> Admission:
    if inp.target is None or inp.stop is None:
        missing = "objectif chiffré" if inp.target is None else "niveau d'invalidation"
        return Admission(Status.NOT_TESTABLE, None, None, None, [f"non testable : {missing} absent"])
    if inp.target <= inp.stop:
        return Admission(Status.NOT_TESTABLE, None, None, None, ["non testable : objectif sous le stop"])
    pmr = p_max_rr(inp.target, inp.stop, inp.fee_fraction, inp.min_reward_risk)
    pmax = pmr if inp.p_max_technical is None else min(pmr, inp.p_max_technical)
    rr = gross_reward_risk(inp.price, inp.target, inp.stop)
    adm = Admission(Status.ADMITTED, pmax, pmr, rr)
    if inp.level_low is not None and not structure_intact(inp.setup, inp.price, inp.level_low):
        what = "support perdu en clôture" if inp.setup == Setup.PULLBACK else "franchissement rendu en clôture"
        adm.status = Status.DEGRADED
        adm.reasons.append(
            f"configuration dégradée : {what} ({inp.price:.4g} < {inp.level_low:.4g}) — "
            "le prix maximal est une condition nécessaire, pas un signal d'entrée"
        )
        return adm
    if inp.price > pmax:
        adm.status = Status.REFUSED
        adm.reasons.append(f"refusé — P_max {pmax:.4g} dépassé de {inp.price / pmax - 1:.2%}")
    return adm
