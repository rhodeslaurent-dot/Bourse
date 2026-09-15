"""Portfolio service: declarations (order entered / executed / stop entered), broker reconciliation,
positions rebuilt from trades. All writes go through here; the domain stays pure."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account, Order, PortfolioState, PositionRow, StopRow, Trade
from app.domain.orders import (
    BrokerStopStatus,
    Execution,
    MatchTolerance,
    Position,
    ProtectionState,
    Side,
    Stop,
    StopKind,
    StopOrderType,
    TradeKind,
    declared_uid,
    match_executions,
    order_state,
)

log = logging.getLogger("bourse.portfolio")


# --- accounts -------------------------------------------------------------------------------


def ensure_accounts(s: Session) -> dict[str, Account]:
    wanted = [
        ("boursobank", "pea", "PEA BoursoBank"),
        ("saxo", "cto_cash", "CTO Saxo (comptant)"),
        ("saxo", "cto_srd", "CTO Saxo (SRD)"),
    ]
    out: dict[str, Account] = {}
    for broker, typ, label in wanted:
        acc = s.scalar(select(Account).where(Account.broker == broker, Account.type == typ))
        if acc is None:
            acc = Account(broker=broker, type=typ, label=label)
            s.add(acc)
            s.flush()
        out[typ] = acc
    return out


def account_by_type(s: Session, typ: str) -> Account:
    acc = s.scalar(select(Account).where(Account.type == typ))
    if acc is None:
        acc = ensure_accounts(s)[typ]
    return acc


# --- positions (rebuilt from trades) ---------------------------------------------------------


def _domain_position(s: Session, row: PositionRow) -> Position:
    p = Position(account_id=row.account_id, isin=row.isin, qty_ordered=row.qty_ordered, mode=row.mode)
    trades = s.scalars(
        select(Trade).where(Trade.position_id == row.id, Trade.deleted_at.is_(None)).order_by(Trade.ts, Trade.id)
    ).all()
    for t in trades:
        if t.kind == "declared" and (t.matched_trade_id is not None or "superseded?" in (t.tags or [])):
            continue  # the confirmed line carries the quantity
        p.apply_execution(_to_exec(t))
    for st in s.scalars(select(StopRow).where(StopRow.position_id == row.id).order_by(StopRow.ts, StopRow.id)):
        stop = Stop(
            st.level,
            StopOrderType(st.order_type),
            st.qty_covered,
            StopKind(st.kind),
            _utc(st.ts),
            BrokerStopStatus(st.broker_status),
            _utc(st.broker_confirmed_at) if st.broker_confirmed_at else None,
        )
        p.stops.append(stop)  # history is replayed as stored (no re-validation of past stops)
        if p.stop_initial is None and st.kind == "initial":
            p.stop_initial = st.level
        if p.protected_at is None and p.protection_state == ProtectionState.PROTECTED:
            p.protected_at = stop.ts
    return p


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _to_exec(t: Trade) -> Execution:
    return Execution(
        t.account_id,
        t.isin,
        Side(t.side),
        t.qty,
        t.price,
        _utc(t.ts),
        TradeKind(t.kind),
        t.declared_uid,
        t.broker_fill_id,
        t.currency,
        t.fx_rate,
        t.fees,
        t.ttf,
        t.mode,
        t.source,
    )


def refresh_position_row(s: Session, row: PositionRow) -> Position:
    p = _domain_position(s, row)
    row.qty_held = p.qty_held
    row.qty_protected = p.qty_protected
    row.avg_price = p.avg_price
    row.execution_state = p.execution_state.value
    row.protection_state = p.protection_state.value
    row.stop_current = p.stop_current
    row.stop_initial = p.stop_initial
    row.opened_at = row.opened_at or p.opened_at
    row.closed_at = p.closed_at
    row.protected_at = row.protected_at or p.protected_at
    row.mode = p.mode
    if p.executions:
        row.currency = p.executions[-1].currency
    return p


def open_position_for(
    s: Session, account_id: int, isin: str, create: bool = False, qty_ordered: int = 0
) -> PositionRow | None:
    row = s.scalar(
        select(PositionRow)
        .where(PositionRow.account_id == account_id, PositionRow.isin == isin, PositionRow.closed_at.is_(None))
        .order_by(PositionRow.id.desc())
    )
    if row is None and create:
        row = PositionRow(account_id=account_id, isin=isin, qty_ordered=qty_ordered)
        s.add(row)
        s.flush()
    return row


# --- declarations ---------------------------------------------------------------------------


@dataclass
class DeclarationResult:
    created: bool
    trade_id: int | None
    position_id: int | None
    message: str


def declare_order(
    s: Session,
    account_id: int,
    isin: str,
    side: str,
    qty: int,
    order_type: str,
    limit_price: float | None,
    trigger_price: float | None,
    validity: str | None,
    source: str,
    proposal_id: int | None = None,
) -> Order:
    """« Ordre saisi » creates an order only — never a position (rule 9)."""
    o = Order(
        account_id=account_id,
        isin=isin,
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        trigger_price=trigger_price,
        validity=validity,
        state="entered",
        source=source,
        entered_at=datetime.now(UTC),
        proposal_id=proposal_id,
    )
    s.add(o)
    s.flush()
    return o


def declare_execution(
    s: Session,
    account_id: int,
    isin: str,
    side: str,
    qty: int,
    price: float,
    ts: datetime,
    source: str,
    mode: str = "comptant",
    order_id: int | None = None,
    proposal_id: int | None = None,
    fees: float = 0.0,
    currency: str = "EUR",
    fx_rate: float = 1.0,
) -> DeclarationResult:
    """« Exécuté » → provisional execution (idempotent by ``declared_uid``) + position (unprotected)."""
    uid = declared_uid(account_id, isin, ts, qty, price)
    existing = s.scalar(select(Trade).where(Trade.declared_uid == uid))
    if existing is None:
        # M10: double click across a minute boundary — same account/isin/side/qty/price within 2 min
        for cand in s.scalars(
            select(Trade).where(
                Trade.kind == "declared",
                Trade.account_id == account_id,
                Trade.isin == isin,
                Trade.side == side,
                Trade.qty == qty,
                Trade.price == price,
            )
        ):
            if abs((_utc(cand.ts) - ts.astimezone(UTC)).total_seconds()) <= 120:
                existing = cand
                break
    if existing is not None:
        return DeclarationResult(
            False, existing.id, existing.position_id, "déclaration déjà enregistrée (double clic ignoré)"
        )
    order = s.get(Order, order_id) if order_id else None
    row = open_position_for(s, account_id, isin, create=True, qty_ordered=order.qty if order else 0)
    assert row is not None
    t = Trade(
        account_id=account_id,
        isin=isin,
        side=side,
        qty=qty,
        price=price,
        ts=ts,
        kind="declared",
        declared_uid=uid,
        source=source,
        mode=mode,
        order_id=order_id,
        proposal_id=proposal_id,
        fees=fees,
        currency=currency,
        fx_rate=fx_rate,
        position_id=row.id,
    )
    s.add(t)
    s.flush()
    if order is not None:
        order.qty_filled += qty
        order.state = order_state(order.qty, order.qty_filled).value
    refresh_position_row(s, row)
    return DeclarationResult(True, t.id, row.id, f"exécution déclarée (provisoire) : {side} {qty} {isin} @ {price}")


def declare_stop(
    s: Session,
    position_id: int,
    level: float,
    order_type: str,
    qty_covered: int,
    kind: str,
    source: str,
    broker_status: str = "active",
) -> Position:
    """« Stop saisi » → protection state by quantity. Raises if the stop would go down."""
    row = s.get(PositionRow, position_id)
    if row is None:
        raise ValueError("position inconnue")
    p = _domain_position(s, row)
    stop = Stop(
        level,
        StopOrderType(order_type),
        qty_covered,
        StopKind(kind),
        datetime.now(UTC),
        BrokerStopStatus(broker_status),
    )
    p.apply_stop(stop)  # validates "never down"
    s.add(
        StopRow(
            position_id=position_id,
            ts=stop.ts,
            level=level,
            order_type=order_type,
            qty_covered=qty_covered,
            kind=kind,
            broker_status=broker_status,
            source=source,
        )
    )
    s.flush()
    return refresh_position_row(s, row)


# --- broker reconciliation ------------------------------------------------------------------


@dataclass
class ReconcileReport:
    matched: int = 0
    discretionary: int = 0
    unmatched_declared: int = 0
    duplicates_ignored: int = 0
    positions_touched: int = 0
    suspected_duplicates: int = 0  # declaration same day/side not matched → superseded, alert
    unassigned_sales: int = 0  # sale without open position → kept « à rapprocher », never dropped


def import_confirmed_fills(s: Session, fills: list[Execution], tol: MatchTolerance) -> ReconcileReport:
    """Broker fills → confirmed trades matched with pending declarations (docs/08 §8.6).

    Unmatched fills are created and tagged ``discretionary?``; a declared execution stays
    provisional until matched (alert after 24 h handled by the monitor job).
    """
    rep = ReconcileReport()
    new_fills = []
    for f in fills:
        if f.broker_fill_id and s.scalar(select(Trade).where(Trade.broker_fill_id == f.broker_fill_id)) is not None:
            rep.duplicates_ignored += 1
            continue
        new_fills.append(f)
    declared_rows = s.scalars(
        select(Trade).where(Trade.kind == "declared", Trade.matched_trade_id.is_(None), Trade.deleted_at.is_(None))
    ).all()
    declared = [_to_exec(r) for r in declared_rows]
    by_uid = {r.declared_uid: r for r in declared_rows}
    pairs, left_d, left_c = match_executions(declared, new_fills, tol)
    touched: set[int] = set()
    for d, c in pairs:
        drow = by_uid[d.declared_uid]
        crow = Trade(
            account_id=c.account_id,
            isin=c.isin,
            side=c.side.value,
            qty=c.qty,
            price=c.price,
            ts=c.ts,
            kind="confirmed",
            broker_fill_id=c.broker_fill_id,
            declared_uid=None,
            source=c.source,
            mode=c.mode,
            fees=c.fees,
            ttf=c.ttf,
            currency=c.currency,
            fx_rate=c.fx_rate,
            position_id=drow.position_id,
            matched_trade_id=drow.id,
            matched_at=datetime.now(UTC),
            order_id=drow.order_id,
            proposal_id=drow.proposal_id,
        )
        s.add(crow)
        s.flush()
        drow.matched_trade_id = crow.id
        drow.matched_at = crow.matched_at
        touched.add(drow.position_id)
        rep.matched += 1
    for c in left_c:
        row = open_position_for(s, c.account_id, c.isin, create=c.side == Side.BUY)
        common = dict(
            account_id=c.account_id,
            isin=c.isin,
            side=c.side.value,
            qty=c.qty,
            price=c.price,
            ts=c.ts,
            kind="confirmed",
            broker_fill_id=c.broker_fill_id,
            source=c.source,
            mode=c.mode,
            fees=c.fees,
            ttf=c.ttf,
            currency=c.currency,
            fx_rate=c.fx_rate,
        )
        if row is None:
            # M11: a sale without an open position is kept, unassigned, for reconciliation — never lost
            s.add(Trade(position_id=None, tags=["sans_position?"], **common))
            rep.unassigned_sales += 1
            log.warning("vente importée sans position ouverte : %s %s (conservée « à rapprocher »)", c.isin, c.qty)
            continue
        tags = ["discretionary?"]
        # C1: an unmatched declaration of the same side on the same position within 24 h is most likely
        # the same fill outside the tolerances → it stops counting (superseded) and both lines are flagged.
        for d in s.scalars(
            select(Trade).where(
                Trade.position_id == row.id,
                Trade.kind == "declared",
                Trade.matched_trade_id.is_(None),
                Trade.side == c.side.value,
            )
        ):
            if "superseded?" in (d.tags or []):
                continue
            if abs((_utc(d.ts) - c.ts).total_seconds()) <= 24 * 3600:
                d.tags = [*(d.tags or []), "superseded?"]
                tags = ["suspected_duplicate?"]
                rep.suspected_duplicates += 1
                break
        s.add(Trade(position_id=row.id, tags=tags, **common))
        touched.add(row.id)
        rep.discretionary += 1
    rep.unmatched_declared = len(left_d)
    for pid in touched:
        row = s.get(PositionRow, pid)
        if row is not None:
            refresh_position_row(s, row)
    rep.positions_touched = len(touched)
    return rep


def update_portfolio_state(
    s: Session,
    account_id: int,
    cash: float | None,
    positions: list[dict],
    orders: list[dict],
    source: str,
    declarative: bool,
) -> PortfolioState:
    st = s.get(PortfolioState, account_id)
    if st is None:
        st = PortfolioState(account_id=account_id)
        s.add(st)
    st.cash = cash
    st.positions_hash = hashlib.sha256(json.dumps(positions, sort_keys=True, default=str).encode()).hexdigest()
    st.orders_hash = hashlib.sha256(json.dumps(orders, sort_keys=True, default=str).encode()).hexdigest()
    st.synced_at = datetime.now(UTC)
    st.source = source
    st.declarative = declarative
    acc = s.get(Account, account_id)
    if acc is not None and cash is not None:
        acc.cash = cash
        acc.cash_updated_at = st.synced_at
        acc.cash_source = source
    return st


def unmatched_declarations_older_than(s: Session, hours: int, now: datetime | None = None) -> list[Trade]:
    now = now or datetime.now(UTC)
    rows = s.scalars(
        select(Trade).where(Trade.kind == "declared", Trade.matched_trade_id.is_(None), Trade.deleted_at.is_(None))
    ).all()
    return [r for r in rows if (now - _utc(r.ts)).total_seconds() >= hours * 3600]
