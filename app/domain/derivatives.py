"""Leveraged product criteria on a stock setup (docs/22 §22.5 rank 4: turbos / mini-futures, long
only). The signal stays on the underlying; this computes, in pure Python, the criteria a product
must meet — knock-out barrier under the slipped stop, indicative leverage — and the quantity for
the risk budget assuming the **whole premium** can be lost. Product prices are not available for
free: the card says « à vérifier chez l'émetteur ». Never an order."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TurboCriteria:
    barrier_max: float  # knock-out barrier must be at or under this level
    leverage_indicative: float  # p_max / (p_max − barrier)
    intrinsic_per_unit: float  # p_max − barrier, per unit of underlying (× parity)
    units_for_budget: int  # quantity of underlying-equivalent so that losing the premium = budget
    note: str


def turbo_criteria(
    p_max: float,
    stop_exec: float,
    risk_budget_eur: float,
    barrier_buffer_pct: float,
    max_leverage: float,
    fx_rate: float = 1.0,
) -> TurboCriteria | None:
    """``None`` when no product can respect both the barrier rule and the leverage cap."""
    if stop_exec >= p_max:
        return None
    barrier = stop_exec * (1 - barrier_buffer_pct)
    if barrier <= 0 or barrier >= p_max:
        return None
    intrinsic = p_max - barrier
    lev = p_max / intrinsic
    if lev > max_leverage:
        barrier = p_max * (1 - 1 / max_leverage)  # a lower barrier to respect the leverage cap
        intrinsic = p_max - barrier
        lev = max_leverage
    units = math.floor(risk_budget_eur / (intrinsic * fx_rate)) if intrinsic > 0 else 0
    return TurboCriteria(
        round(barrier, 4),
        round(lev, 2),
        round(intrinsic, 4),
        units,
        "barrière désactivante sous le stop glissé ; perte maximale = prime entière ; "
        "prix et produit à vérifier chez l'émetteur",
    )
