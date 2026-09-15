"""``portfolio_sync`` (19:00, full) and ``portfolio_sync_intraday`` (every 5 min in session, also
after each declaration and before any proposal): Saxo **read** → confirmed fills matched with
declarations, stops' broker status, cash, dated ``portfolio_state`` (docs/08 §8.6)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.data.providers.saxo import BrokerOrderDto, BrokerPositionDto, SaxoClient, SaxoPortfolio
from app.db.models import BrokerSyncRun, PositionRow, StopRow
from app.db.session import db_session
from app.domain.orders import Execution, MatchTolerance, Side, TradeKind
from app.scheduler.runner import JobContext
from app.services.portfolio import account_by_type, import_confirmed_fills, update_portfolio_state

log = logging.getLogger("bourse.jobs.portfolio_sync")

STOP_TYPES = ("Stop", "StopLimit", "TrailingStop", "StopIfTraded")


def fills_from_positions(positions: list[BrokerPositionDto], account_id: int) -> list[Execution]:
    """Each Saxo position is an opening fill (``PositionId`` = broker_fill_id)."""
    out = []
    for p in positions:
        if not p.isin:
            continue
        out.append(
            Execution(
                account_id,
                p.isin,
                Side.BUY,
                abs(p.qty),
                p.open_price,
                p.opened_at,
                TradeKind.CONFIRMED,
                broker_fill_id=f"saxo-pos-{p.position_id}",
                currency=p.currency,
                mode="srd" if p.srd else "comptant",
                source="saxo_api",
            )
        )
    return out


def sync_saxo(portfolio: SaxoPortfolio, tolerance: MatchTolerance) -> dict[str, object]:
    positions = portfolio.positions()
    orders = portfolio.orders()
    cash = portfolio.cash()
    with db_session() as s:
        cash_acc = account_by_type(s, "cto_cash")
        srd_acc = account_by_type(s, "cto_srd")
        fills = fills_from_positions([p for p in positions if not p.srd], cash_acc.id) + fills_from_positions(
            [p for p in positions if p.srd], srd_acc.id
        )
        rep = import_confirmed_fills(s, fills, tolerance)
        stops_updated = _apply_broker_stops(s, orders, [cash_acc.id, srd_acc.id])
        for acc in (cash_acc, srd_acc):
            update_portfolio_state(
                s,
                acc.id,
                cash,
                [p.__dict__ for p in positions],
                [o.__dict__ for o in orders],
                "saxo_api",
                declarative=False,
            )
        diffs = {
            "matched": rep.matched,
            "discretionary": rep.discretionary,
            "unmatched_declared": rep.unmatched_declared,
            "stops_updated": stops_updated,
            "orders_open": len(orders),
        }
        s.add(
            BrokerSyncRun(broker="saxo", ts=datetime.now(UTC), status="ok", positions_count=len(positions), diffs=diffs)
        )
    return diffs


def _apply_broker_stops(s, orders: list[BrokerOrderDto], account_ids: list[int]) -> int:  # type: ignore[no-untyped-def]
    """Broker stop orders confirm (or create) protection rows by quantity."""
    n = 0
    for o in orders:
        if o.order_type not in STOP_TYPES or o.side != "sell" or not o.isin:
            continue
        row = s.scalar(
            select(PositionRow)
            .where(PositionRow.isin == o.isin, PositionRow.account_id.in_(account_ids), PositionRow.closed_at.is_(None))
            .order_by(PositionRow.id.desc())
        )
        if row is None:
            continue
        existing = s.scalar(select(StopRow).where(StopRow.broker_order_id == o.order_id))
        level = float(o.stop_price or o.price or 0)
        if existing is None:
            s.add(
                StopRow(
                    position_id=row.id,
                    ts=datetime.now(UTC),
                    level=level,
                    order_type="plage" if o.order_type == "StopLimit" else "seuil",
                    qty_covered=o.qty - o.filled,
                    kind="manual",
                    broker_status="active",
                    broker_confirmed_at=datetime.now(UTC),
                    broker_order_id=o.order_id,
                    source="saxo_api",
                )
            )
        else:
            existing.broker_status = (
                "active" if o.status.lower() in ("working", "placed", "") else o.status.lower()[:10]
            )
            existing.broker_confirmed_at = datetime.now(UTC)
            existing.qty_covered = o.qty - o.filled
        n += 1
    s.flush()
    from app.services.portfolio import refresh_position_row

    for row in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None))):
        refresh_position_row(s, row)
    return n


def run(ctx: JobContext) -> int:
    import os

    if not (os.environ.get("SAXO_TOKEN_KEY") or os.environ.get("SAXO_ACCESS_TOKEN")):
        log.info("Saxo non configuré : synchronisation ignorée (état déclaratif)")
        return 0
    tol = ctx.config.params.accounts.cto.match_tolerance
    diffs = sync_saxo(
        SaxoPortfolio(SaxoClient()), MatchTolerance(float(tol.get("price_pct", 0.005)), int(tol.get("minutes", 10)))
    )
    return int(diffs["matched"]) + int(diffs["discretionary"])
