"""Equity and exposure with SRD commitments (docs/08 §8.6, docs/10 §10.3). Pure."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PositionValue:
    isin: str
    qty: int
    avg_price: float  # EUR, PMP frais inclus
    last_price: float  # devise de cotation
    fx_rate: float = 1.0  # devise → EUR
    mode: str = "comptant"  # comptant | srd
    crd_accrued: float = 0.0  # CRD courue (SRD)
    currency: str = "EUR"

    @property
    def market_value_eur(self) -> float:
        return self.qty * self.last_price * self.fx_rate

    @property
    def unrealized_pnl_eur(self) -> float:
        return self.market_value_eur - self.qty * self.avg_price


@dataclass
class AccountEquity:
    cash: float
    cash_positions_value: float
    srd_unrealized_pnl: float
    srd_notional: float
    crd_accrued: float
    equity: float
    exposure_total: float
    fx_exposure: float = 0.0
    by_currency: dict[str, float] = field(default_factory=dict)


def account_equity(cash: float, positions: list[PositionValue]) -> AccountEquity:
    """Equity = cash + cash positions + SRD unrealized P&L − accrued CRD. SRD notional is a
    *commitment*, never an asset; it is reported separately for leverage/coverage."""
    cash_val = sum(p.market_value_eur for p in positions if p.mode == "comptant")
    srd_pnl = sum(p.unrealized_pnl_eur for p in positions if p.mode == "srd")
    srd_notional = sum(p.market_value_eur for p in positions if p.mode == "srd")
    crd = sum(p.crd_accrued for p in positions if p.mode == "srd")
    by_ccy: dict[str, float] = {}
    for p in positions:
        by_ccy[p.currency] = by_ccy.get(p.currency, 0.0) + p.market_value_eur
    fx_exp = sum(v for c, v in by_ccy.items() if c != "EUR")
    equity = cash + cash_val + srd_pnl - crd
    return AccountEquity(cash, cash_val, srd_pnl, srd_notional, crd, equity, cash_val + srd_notional, fx_exp, by_ccy)


def srd_leverage(srd_notional: float, cto_equity: float) -> float | None:
    return None if cto_equity <= 0 else srd_notional / cto_equity


def srd_coverage(
    cash: float, equities_value: float, srd_notional: float, coverage_rates: dict[str, float], broker_rate: float | None
) -> tuple[float, float, float | None]:
    """(required, available, ratio). Required = notional × broker rate (or cash rate if unknown);
    available = cash + equities × equities rate (AMF: cash 20 %, bonds 25 %, equities 40 %)."""
    rate = broker_rate if broker_rate is not None else coverage_rates.get("cash", 0.20)
    required = srd_notional * rate
    available = cash + equities_value * (1 - coverage_rates.get("equities", 0.40))
    ratio = None if required <= 0 else available / required
    return required, available, ratio
