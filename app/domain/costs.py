"""Full cost of a trade (docs/08 §8.7, docs/22 R11): net gain at target and net loss at stop per
account. Pure, deterministic, every rate comes from dated parameters (CLAUDE.md rules 3 and 5).

Buy leg: commission + French TTF (official list, issuer seat) + foreign transaction tax + FX fee.
Sell leg: commission + FX fee. SRD carry: daily rate × buy notional × days. Tax: envelope rate on
the positive pre-tax gain; a loss earns no tax credit unless ``assume_loss_offset`` (prudent).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.risk import FeeSchedule


@dataclass(frozen=True)
class CostParams:
    fees: FeeSchedule
    ttf_rate: float = 0.0  # 0 when the issuer is not on the official TTF list
    ftt_rate: float = 0.0  # foreign transaction tax on purchases (tax.ftt_by_country)
    fx_fee_pct: float = 0.0  # per conversion (brokers.<b>.fx_fee_pct), 0 for EUR
    srd_daily_rate: float = 0.0  # srd.crd_daily_rate when bought on SRD, else 0
    holding_days: int = 0  # horizon used for the SRD carry
    tax_rate: float = 0.0  # envelope rate on gains (PFU, PEA social contributions…)
    assume_loss_offset: bool = False  # tax.assume_loss_offset


@dataclass(frozen=True)
class NetOutcome:
    buy_notional_eur: float
    fees_buy_eur: float
    fees_sell_target_eur: float
    fees_sell_stop_eur: float
    carry_eur: float
    gain_gross_eur: float
    tax_eur: float
    gain_net_eur: float
    gain_net_pct: float
    loss_net_eur: float
    loss_net_pct: float
    net_reward_risk: float | None


def buy_fees(notional_eur: float, p: CostParams) -> float:
    return p.fees.commission(notional_eur) + notional_eur * (p.ttf_rate + p.ftt_rate + p.fx_fee_pct)


def sell_fees(notional_eur: float, p: CostParams) -> float:
    return p.fees.commission(notional_eur) + notional_eur * p.fx_fee_pct


def round_trip_fee_fraction(notional_eur: float, p: CostParams) -> float:
    """Fees (buy + sell at the same notional + carry) as a fraction of the notional; used by the
    maximum entry price formula (docs/07 §7.9.1)."""
    if notional_eur <= 0:
        return 0.0
    carry = p.srd_daily_rate * notional_eur * p.holding_days
    return (buy_fees(notional_eur, p) + sell_fees(notional_eur, p) + carry) / notional_eur


def net_outcome(
    shares: int, entry: float, target: float, stop_exec: float, p: CostParams, fx_rate: float = 1.0
) -> NetOutcome:
    """Net result of ``shares`` bought at ``entry`` (the maximum entry price), sold at ``target``
    or at the slipped stop ``stop_exec``. Prices in quote currency, amounts in EUR."""
    buy = shares * entry * fx_rate
    f_buy = buy_fees(buy, p)
    sell_t = shares * target * fx_rate
    sell_s = shares * stop_exec * fx_rate
    f_t = sell_fees(sell_t, p)
    f_s = sell_fees(sell_s, p)
    carry = p.srd_daily_rate * buy * p.holding_days
    gross = sell_t - buy
    pre_tax = gross - f_buy - f_t - carry
    tax = max(0.0, pre_tax) * p.tax_rate
    gain = pre_tax - tax
    loss = buy - sell_s + f_buy + f_s + carry
    if p.assume_loss_offset and loss > 0:
        loss *= 1 - p.tax_rate
    rr = gain / loss if loss > 0 else None
    return NetOutcome(
        buy,
        f_buy,
        f_t,
        f_s,
        carry,
        gross,
        tax,
        gain,
        gain / buy if buy else 0.0,
        loss,
        loss / buy if buy else 0.0,
        rr,
    )


def envelope_tax_rate(
    account: str, pfu_total_rate: float, pea_social_rate: float, pea_age_years: float | None
) -> float:
    """Rate applied to a gain for ranking (docs/08 §8.7): PEA ≥ 5 years → social contributions only;
    PEA younger or of unknown age → PFU (prudent); CTO (cash or SRD) → PFU."""
    if account == "pea" and pea_age_years is not None and pea_age_years >= 5:
        return pea_social_rate
    return pfu_total_rate
