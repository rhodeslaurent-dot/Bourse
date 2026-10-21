"""Performance arithmetic (docs/10, docs/17 §17.5). Pure functions, no I/O.

All amounts are in EUR: the caller converts with a known FX rate or leaves the leg out (never 1.0
by default). Realized P&L uses FIFO lots per position; fees and TTF are costs of the leg.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Leg:
    side: str  # buy | sell
    qty: int
    price_eur: float
    fees_eur: float
    ts: datetime


@dataclass
class PositionResult:
    realized_pnl: float = 0.0  # after fees and TTF of the closed quantity
    fees: float = 0.0
    closed_qty: int = 0
    open_qty: int = 0
    open_cost: float = 0.0  # cost basis of remaining lots, buy fees included
    first_buy: datetime | None = None
    last_sell: datetime | None = None
    sells: list[tuple[datetime, float]] = field(default_factory=list)  # (ts, realized pnl of that sell)


def realize_fifo(legs: list[Leg]) -> PositionResult:
    """FIFO matching of sells against buys. A sell beyond the held quantity is ignored for P&L
    (orphan sale, already flagged elsewhere)."""
    res = PositionResult()
    lots: deque[list[float]] = deque()  # [qty, unit cost incl. buy fees]
    for leg in sorted(legs, key=lambda x: x.ts):
        res.fees += leg.fees_eur
        if leg.side == "buy":
            if leg.qty <= 0:
                continue
            res.first_buy = res.first_buy or leg.ts
            lots.append([float(leg.qty), leg.price_eur + leg.fees_eur / leg.qty])
            continue
        remaining = float(leg.qty)
        matched = 0.0
        cost = 0.0
        while remaining > 0 and lots:
            take = min(remaining, lots[0][0])
            cost += take * lots[0][1]
            lots[0][0] -= take
            remaining -= take
            matched += take
            if lots[0][0] <= 0:
                lots.popleft()
        if matched <= 0:
            continue
        sell_fees = leg.fees_eur * (matched / leg.qty) if leg.qty else 0.0
        pnl = matched * leg.price_eur - sell_fees - cost
        res.realized_pnl += pnl
        res.closed_qty += int(matched)
        res.last_sell = leg.ts
        res.sells.append((leg.ts, pnl))
    res.open_qty = int(sum(lot[0] for lot in lots))
    res.open_cost = sum(lot[0] * lot[1] for lot in lots)
    return res


def r_multiple(pnl: float, initial_risk_eur: float | None) -> float | None:
    """P&L in units of the initial risk; ``None`` when the risk is unknown or not positive."""
    if initial_risk_eur is None or initial_risk_eur <= 0:
        return None
    return pnl / initial_risk_eur


def latent_pnl(qty: int, avg_price: float, last_price: float | None, exit_fee_eur: float = 0.0) -> float | None:
    """(last − average cost) × qty − estimated exit fee; ``None`` without a price (never estimated)."""
    if last_price is None or qty <= 0:
        return None
    return (last_price - avg_price) * qty - exit_fee_eur


@dataclass(frozen=True)
class ClosedTrade:
    isin: str
    account: str
    closed: date
    pnl: float
    r: float | None
    days_held: int | None
    detector: str | None = None
    mode: str = "comptant"


@dataclass(frozen=True)
class QualityStats:
    n: int
    wins: int
    losses: int
    win_rate: float | None
    avg_win: float | None
    avg_loss: float | None
    avg_win_r: float | None
    avg_loss_r: float | None
    expectancy_r: float | None
    expectancy_eur: float | None
    profit_factor: float | None
    total: float
    reliability: str  # "aucun trade" | "indicatif (< N trades)" | "significatif"


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def quality(trades: list[ClosedTrade], min_significant: int = 30) -> QualityStats:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    rs = [t.r for t in trades if t.r is not None]
    n = len(trades)
    reliability = (
        "aucun trade"
        if n == 0
        else (f"indicatif (< {min_significant} trades)" if n < min_significant else "significatif")
    )
    return QualityStats(
        n=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / n if n else None,
        avg_win=_mean([t.pnl for t in wins]),
        avg_loss=_mean([t.pnl for t in losses]),
        avg_win_r=_mean([t.r for t in wins if t.r is not None]),
        avg_loss_r=_mean([t.r for t in losses if t.r is not None]),
        expectancy_r=_mean(rs),
        expectancy_eur=_mean([t.pnl for t in trades]),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        total=sum(t.pnl for t in trades),
        reliability=reliability,
    )


def by_month(trades: list[ClosedTrade]) -> dict[str, float]:
    """Realized P&L per calendar month ``YYYY-MM`` (closing date), ascending."""
    out: dict[str, float] = {}
    for t in sorted(trades, key=lambda x: x.closed):
        k = t.closed.strftime("%Y-%m")
        out[k] = out.get(k, 0.0) + t.pnl
    return out


def group_total(trades: list[ClosedTrade], key: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        k = str(getattr(t, key) or "—")
        out[k] = out.get(k, 0.0) + t.pnl
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def max_drawdown(equity: list[float]) -> float | None:
    """Largest fall from a running peak, as a fraction of that peak (0.1 = −10 %)."""
    peak: float | None = None
    worst = 0.0
    for v in equity:
        if peak is None or v > peak:
            peak = v
        if peak and peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst if equity else None
