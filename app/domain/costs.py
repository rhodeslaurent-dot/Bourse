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
    net_reward_risk: float | None  # after tax: shown and ranks (docs/08 §8.7)
    # net of fees only (before tax): the ratio of R4 that the gates compare (docs/07 §7.9.1, §7.9.4)
    fee_net_reward_risk: float | None = None


def foreign_tax(spec: object, market_cap_eur: float | None) -> tuple[float, str]:
    """Foreign transaction tax on a purchase (``tax.ftt_by_country[<ISIN prefix>]``): a bare rate applies to
    every issuer of the country; ``{rate, min_market_cap_eur}`` only above the cap (Spain 1 bn€, Italy 500 M€,
    assessed by each country on a reference date — the current cap is a presumption). An unknown cap keeps
    the tax (prudence). Returns (rate, basis)."""
    if spec is None:
        return 0.0, "aucune taxe étrangère"
    if isinstance(spec, int | float):
        return float(spec), "taux du pays d'émission"
    if not isinstance(spec, dict):
        return 0.0, "paramètre illisible"
    rate = float(spec.get("rate") or 0.0)
    floor = spec.get("min_market_cap_eur")
    if floor is None:
        return rate, "taux du pays d'émission"
    if market_cap_eur is None:
        return rate, "capitalisation inconnue : taxe retenue par prudence"
    if market_cap_eur >= float(floor):
        return rate, f"capitalisation ≥ {float(floor) / 1e9:g} Md€ (présomption sur la capitalisation actuelle)"
    return 0.0, f"capitalisation < {float(floor) / 1e9:g} Md€ : non due (présomption)"


def ftt_rate_any(spec: object) -> float:
    """The rate of a ``tax.ftt_by_country`` entry whatever its form, threshold ignored: historical studies (backtest,
    research) have no point-in-time market cap, and their registered runs must replay with the same costs."""
    if isinstance(spec, int | float):
        return float(spec)
    if isinstance(spec, dict):
        return float(spec.get("rate") or 0.0)
    return 0.0


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
    fee_rr = pre_tax / loss if loss > 0 else None
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
        fee_rr,
    )


def envelope_tax_rate(
    account: str,
    pfu_total_rate: float,
    pea_social_rate: float,
    pea_age_years: float | None,
    no_withdrawal_before_5y: bool = False,
) -> float:
    """Rate applied to a gain for ranking (docs/08 §8.7): PEA ≥ 5 years → social contributions only; a
    younger PEA too when the user declared no withdrawal before its 5 years (``pea.no_withdrawal_before_5y``,
    decision of 25/10/2026: the gains stay in the envelope and only the social contributions are due at the
    withdrawal); otherwise PFU (prudent); CTO (cash or SRD) → PFU."""
    if account == "pea" and (no_withdrawal_before_5y or (pea_age_years is not None and pea_age_years >= 5)):
        return pea_social_rate
    return pfu_total_rate


@dataclass(frozen=True)
class AltListing:
    """Executing a non-euro value on a secondary euro listing (Xetra) instead of its primary venue
    (user decision of 26/10/2026, docs/32 §32.3): primary venue first, the euro listing only when the
    change fee it avoids weighs in the expected gain."""

    listing: str
    fx_saving_eur: float  # both conversions avoided, before tax
    share_of_gain: float | None  # of the net gain at the target; None when that gain is not positive
    recommended: bool


def alt_listing_saving(
    listing: str, buy_notional_eur: float, sell_notional_eur: float, gain_net_eur: float, fx_fee_pct: float,
    min_share_of_gain: float,
) -> AltListing | None:  # fmt: skip
    """Change fees avoided by trading ``listing`` in euros: one conversion per leg (buy at the maximum entry
    price, sell at the target). None when the value trades in euros already (no fee to avoid)."""
    if fx_fee_pct <= 0 or buy_notional_eur <= 0:
        return None
    saving = fx_fee_pct * (buy_notional_eur + max(0.0, sell_notional_eur))
    share = saving / gain_net_eur if gain_net_eur > 0 else None
    return AltListing(listing, saving, share, share is not None and share >= min_share_of_gain)
