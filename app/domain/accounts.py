"""Account costs (docs/08 §8.3): SRD trade cost over an horizon; TTF applicability. Pure."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.risk import FeeSchedule


@dataclass(frozen=True)
class SrdCostParams:
    crd_daily_rate: float
    prorogation_rate: float
    prorogation_min_eur: float
    max_cost_pct_of_target1: float


@dataclass(frozen=True)
class SrdCost:
    commission_eur: float
    crd_eur: float
    prorogations_eur: float
    ttf_eur: float
    total_eur: float
    pct_of_notional: float
    pct_of_gain_t1: float | None
    blocked: str | None


def ttf_applicable(country: str, market_cap_eur: float | None, threshold_eur: float) -> bool:
    """TTF 0,4 % : actions françaises, capitalisation > seuil (docs/11 §11.1); applies in PEA too."""
    return country == "FR" and market_cap_eur is not None and market_cap_eur > threshold_eur


def srd_trade_cost(
    notional_eur: float,
    days_held: int,
    prorogations: int,
    fees: FeeSchedule,
    params: SrdCostParams,
    ttf_rate: float,
    gain_at_target1_eur: float | None,
) -> SrdCost:
    commission = 2 * fees.commission(notional_eur)
    crd = params.crd_daily_rate * notional_eur * days_held
    pro = prorogations * max(params.prorogation_rate * notional_eur, params.prorogation_min_eur)
    ttf = notional_eur * ttf_rate
    total = commission + crd + pro + ttf
    pct_gain = (total / gain_at_target1_eur) if gain_at_target1_eur else None
    blocked = None
    if pct_gain is not None and pct_gain > params.max_cost_pct_of_target1:
        blocked = f"BLOQUÉ : coût SRD {total:.0f} € = {pct_gain:.0%} du gain à l'objectif 1 > {params.max_cost_pct_of_target1:.0%} (risk.srd_max_cost_pct_of_target1)"  # noqa: E501
    return SrdCost(commission, crd, pro, ttf, total, total / notional_eur if notional_eur else 0.0, pct_gain, blocked)
