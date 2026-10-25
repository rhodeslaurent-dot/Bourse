"""Performance data access (docs/17 §17.5): trades → FIFO legs in EUR, closed trades, latent P&L
with price status, costs, equity snapshots (``kpi_daily``). Arithmetic lives in app.domain.performance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import Account, KpiDaily, PositionRow, PriceEod, Trade
from app.domain.performance import (
    ClosedTrade,
    Leg,
    by_month,
    group_total,
    latent_pnl,
    max_drawdown,
    quality,
    r_multiple,
    realize_fifo,
)
from app.services.instruments import name_of
from app.services.market_data import fx_to_eur
from app.services.risk_view import fee_schedule_for

BROKER_OF = {"pea": "boursobank", "cto_cash": "saxo", "cto_srd": "saxo"}


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _counted(t: Trade) -> bool:
    """Same rule as the position engine: a matched or superseded declaration does not count."""
    return not (t.kind == "declared" and (t.matched_trade_id is not None or "superseded?" in (t.tags or [])))


def _last_price(s: Session, row: PositionRow) -> tuple[float | None, str, datetime | date | None]:
    if row.last_price is not None:
        return row.last_price, row.last_price_status or "indisponible", row.last_price_at
    eod = s.scalar(select(PriceEod).where(PriceEod.isin == row.isin).order_by(PriceEod.day.desc()))
    if eod is not None:
        return eod.close, "eod", eod.day
    return None, "unavailable", None


@dataclass
class OpenLine:
    isin: str
    name: str
    account: str
    mode: str
    qty: int
    avg_price: float
    last_price: float | None
    status: str
    as_of: Any
    latent: float | None
    latent_pct: float | None
    stop: float | None
    risk_to_stop: float | None  # (last − stop) × qty, what is still at stake


@dataclass
class PerformanceView:
    closed: list[ClosedTrade] = field(default_factory=list)
    closed_names: dict[str, str] = field(default_factory=dict)
    open_lines: list[OpenLine] = field(default_factory=list)
    realized_total: float = 0.0
    latent_total: float = 0.0
    fees_total: float = 0.0
    costs_by_month: dict[str, float] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    equity: list[tuple[date, float | None]] = field(default_factory=list)
    equity_incomplete_days: int = 0
    drawdown: float | None = None


def build_performance(s: Session, config: LoadedConfig) -> dict[str, Any]:
    accounts = {a.id: a for a in s.scalars(select(Account))}
    v = PerformanceView()
    for row in s.scalars(select(PositionRow).order_by(PositionRow.id)):
        acc = accounts.get(row.account_id)
        acc_type = acc.type if acc else "?"
        trades = [
            t
            for t in s.scalars(
                select(Trade)
                .where(Trade.position_id == row.id, Trade.deleted_at.is_(None))
                .order_by(Trade.ts, Trade.id)
            )
            if _counted(t)
        ]
        if not trades:
            continue
        legs: list[Leg] = []
        fx_missing = False
        for t in trades:
            fx = fx_to_eur(s, t.currency or "EUR", _utc(t.ts).date())
            if fx is None:
                fx_missing = True
                break
            legs.append(Leg(t.side, t.qty, t.price * fx, (t.fees or 0.0) + (t.ttf or 0.0), _utc(t.ts)))
            month = _utc(t.ts).strftime("%Y-%m")
            v.costs_by_month[month] = v.costs_by_month.get(month, 0.0) + (t.fees or 0.0) + (t.ttf or 0.0)
        if fx_missing:
            v.incomplete.append(f"{row.isin} : taux de change indisponible, exclu")
            continue
        res = realize_fifo(legs)
        v.fees_total += res.fees
        if res.closed_qty > 0 and res.last_sell is not None:
            buys = [lg for lg in legs if lg.side == "buy"]
            entry = sum(lg.qty * lg.price_eur for lg in buys) / max(1, sum(lg.qty for lg in buys))
            risk = (
                (entry - row.stop_initial) * res.closed_qty if row.stop_initial and row.stop_initial < entry else None
            )
            v.closed.append(
                ClosedTrade(
                    isin=row.isin,
                    account=acc_type,
                    closed=res.last_sell.date(),
                    pnl=res.realized_pnl,
                    r=r_multiple(res.realized_pnl, risk),
                    days_held=(res.last_sell - res.first_buy).days if res.first_buy else None,
                    detector=next((t.detector for t in trades if t.side == "buy" and t.detector), None)
                    or ("discrétionnaire" if any("discretionary?" in (t.tags or []) for t in trades) else None),
                    mode=row.mode,
                )
            )
            v.closed_names[row.isin] = name_of(row.isin)
            v.realized_total += res.realized_pnl
        if row.qty_held > 0 and row.closed_at is None:
            price, status, as_of = _last_price(s, row)
            fx_now = fx_to_eur(s, row.currency or "EUR")  # latent in EUR like the realised legs
            fee = (
                fee_schedule_for(config, BROKER_OF.get(acc_type, "saxo")).commission(
                    row.qty_held * price * (fx_now or 1.0)
                )
                if price
                else 0.0
            )
            lat = (
                latent_pnl(row.qty_held, row.avg_price * fx_now, price * fx_now, fee)
                if fx_now is not None and price is not None
                else None
            )
            if fx_now is None:
                v.incomplete.append(f"{row.isin} : taux de change indisponible, latent exclu (jamais estimé)")
            elif lat is None:
                v.incomplete.append(f"{row.isin} : cours indisponible, latent exclu (jamais estimé)")
            else:
                v.latent_total += lat
            v.open_lines.append(
                OpenLine(
                    isin=row.isin,
                    name=name_of(row.isin),
                    account=acc_type,
                    mode=row.mode,
                    qty=row.qty_held,
                    avg_price=row.avg_price,
                    last_price=price,
                    status=status,
                    as_of=as_of,
                    latent=lat,
                    latent_pct=(price / row.avg_price - 1) if price and row.avg_price else None,
                    stop=row.stop_current,
                    risk_to_stop=(price - row.stop_current) * row.qty_held if price and row.stop_current else None,
                )
            )
    kpis = list(s.scalars(select(KpiDaily).where(KpiDaily.account_id.is_(None)).order_by(KpiDaily.day)))
    v.equity = [(k.day, k.equity) for k in kpis]
    v.equity_incomplete_days = sum(1 for k in kpis if k.incomplete)
    v.drawdown = max_drawdown([e for _, e in v.equity if e is not None])
    return {
        "v": v,
        "q": quality(v.closed),
        "months": by_month(v.closed),
        "by_account": group_total(v.closed, "account"),
        "by_detector": group_total(v.closed, "detector"),
        "by_mode": group_total(v.closed, "mode"),
        "capital": float(config.params.capital.capital_pilote_eur),
    }


def snapshot_equity(s: Session, config: LoadedConfig, day: date, now: datetime | None = None) -> list[KpiDaily]:
    """Equity per account and consolidated for ``day`` (docs/10 §10.3): cash + comptant valued +
    SRD latent. Unknown cash → equity ``None`` for that account; a position without price is
    excluded and the row marked incomplete. Idempotent per (day, account)."""
    now = now or datetime.now(UTC)
    data = build_performance(s, config)
    view: PerformanceView = data["v"]
    rows: list[KpiDaily] = []
    total_equity: float | None = 0.0
    total_cash = 0.0
    total_pos = 0.0
    total_lat = 0.0
    any_incomplete = bool(view.incomplete)
    for acc in s.scalars(select(Account).order_by(Account.id)):
        lines = [ln for ln in view.open_lines if ln.account == acc.type]
        pos_value = sum(ln.qty * ln.last_price for ln in lines if ln.mode != "srd" and ln.last_price is not None)
        lat = sum(ln.latent for ln in lines if ln.latent is not None)
        srd_lat = sum(ln.latent for ln in lines if ln.mode == "srd" and ln.latent is not None)
        incomplete = any(ln.last_price is None for ln in lines) or acc.cash is None
        equity = None if acc.cash is None else acc.cash + pos_value + srd_lat
        realized = sum(t.pnl for t in view.closed if t.account == acc.type)
        rows.append(_upsert_kpi(s, day, acc.id, equity, acc.cash, pos_value, lat, realized, incomplete, now))
        any_incomplete = any_incomplete or incomplete
        if equity is None:
            total_equity = total_equity  # unknown account cash: consolidated stays partial, flagged
        else:
            total_equity = (total_equity or 0.0) + equity
        total_cash += acc.cash or 0.0
        total_pos += pos_value
        total_lat += lat
    rows.append(
        _upsert_kpi(
            s, day, None, total_equity, total_cash, total_pos, total_lat, view.realized_total, any_incomplete, now
        )
    )
    s.flush()
    return rows


def _upsert_kpi(
    s: Session,
    day: date,
    account_id: int | None,
    equity: float | None,
    cash: float | None,
    pos: float,
    lat: float,
    realized: float,
    incomplete: bool,
    now: datetime,
) -> KpiDaily:  # noqa: E501, PLR0913
    q = select(KpiDaily).where(KpiDaily.day == day)
    q = q.where(KpiDaily.account_id.is_(None)) if account_id is None else q.where(KpiDaily.account_id == account_id)
    row = s.scalar(q)
    if row is None:
        row = KpiDaily(day=day, account_id=account_id, computed_at=now)
        s.add(row)
    row.equity, row.cash, row.positions_value, row.latent_pnl = equity, cash, pos, lat
    row.realized_pnl_cum, row.incomplete, row.computed_at = realized, incomplete, now
    return row
