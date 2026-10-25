"""Brokerage formula comparison (BoursoBank Découverte / Classic / Trader / Ultimate Trader): the cost of a
month of orders under each formula, with its monthly conditions, and the recommendation. Pure functions."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.risk import FeeSchedule


@dataclass(frozen=True)
class Formula:
    name: str
    label: str
    fees: FeeSchedule  # per order, PEA cap included when the account is a PEA
    fee_if_no_order: float = 0.0  # monthly fee when no order is executed in the month
    min_orders: int = 0  # below this number of orders in the month…
    fee_if_below: float = 0.0  # … this monthly fee is due
    available: bool = True  # False when the formula is not offered on this account
    note: str = ""


def month_cost(f: Formula, orders_eur: list[float]) -> float:
    cost = sum(f.fees.commission(x) for x in orders_eur)
    if not orders_eur:
        cost += f.fee_if_no_order
    if f.min_orders and len(orders_eur) < f.min_orders:
        cost += f.fee_if_below
    return cost


@dataclass
class PlanComparison:
    months: int
    orders: int
    costs: dict[str, float]  # formula → average monthly cost
    current: str
    best: str | None
    saving_per_month: float
    recommendation: str
    details: list[str] = field(default_factory=list)


def compare(
    formulas: list[Formula], months: list[list[float]], current: str, min_saving_eur: float = 5.0
) -> PlanComparison:
    """``months`` = the order amounts of each month considered (real, or projected). The best available
    formula is recommended only if it saves at least ``min_saving_eur`` a month on average."""
    n = max(1, len(months))
    costs = {f.name: sum(month_cost(f, m) for m in months) / n for f in formulas if f.available}
    orders = sum(len(m) for m in months)
    if current not in costs:
        return PlanComparison(len(months), orders, costs, current, None, 0.0, "formule actuelle inconnue")
    best = min(costs, key=lambda k: costs[k])
    saving = costs[current] - costs[best]
    labels = {f.name: f.label for f in formulas}
    if best == current or saving < min_saving_eur:
        keep = labels.get(current, current)
        text = f"Garder {keep} : aucune formule ne fait gagner au moins {min_saving_eur:.0f} € par mois"
        return PlanComparison(len(months), orders, costs, current, current, max(0.0, saving), text)
    text = (
        f"Passer de {labels.get(current, current)} à {labels.get(best, best)} : environ {saving:.0f} € de frais "
        f"en moins par mois ({saving * 12:.0f} € par an) au rythme de {orders / n:.1f} ordre(s) par mois"
    )
    return PlanComparison(len(months), orders, costs, current, best, saving, text)
