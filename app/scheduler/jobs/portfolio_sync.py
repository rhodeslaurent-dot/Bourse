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
# Saxo order statuses → BrokerStopStatus (M8): never crash on an unknown value
STATUS_MAP = {
    "working": "active",
    "placed": "active",
    "parked": "active",
    "pending": "active",
    "filled": "executed",
    "cancelled": "expired",
    "expired": "expired",
    "rejected": "rejected",
    "notworking": "rejected",
    "unknown": "unknown",
}


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


def sell_fills_from_closed(closed: list[dict], cash_id: int, srd_id: int) -> list[Execution]:
    out = []
    for c in closed:
        if not c["isin"] or c["qty"] <= 0:
            continue
        out.append(
            Execution(
                srd_id if c["srd"] else cash_id,
                c["isin"],
                Side.SELL,
                c["qty"],
                c["price"],
                c["ts"],
                TradeKind.CONFIRMED,
                broker_fill_id=f"saxo-close-{c['fill_id']}",
                currency=c["currency"],
                mode="srd" if c["srd"] else "comptant",
                source="saxo_api",
            )
        )
    return out


def sync_saxo(
    portfolio: SaxoPortfolio, tolerance: MatchTolerance, alerts=None, mode: str = "reunion"
) -> dict[str, object]:  # type: ignore[no-untyped-def]
    positions = portfolio.positions()
    orders = portfolio.orders()
    cash = portfolio.cash()
    try:
        closed = portfolio.closed_fills()
    except Exception as exc:  # noqa: BLE001
        log.warning("closedpositions indisponible : %s", exc)
        closed = []
    with db_session() as s:
        cash_acc = account_by_type(s, "cto_cash")
        srd_acc = account_by_type(s, "cto_srd")
        fills = fills_from_positions([p for p in positions if not p.srd], cash_acc.id) + fills_from_positions(
            [p for p in positions if p.srd], srd_acc.id
        )
        fills += sell_fills_from_closed(closed, cash_acc.id, srd_acc.id)  # C3: sales are imported too
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
        # C3: positions held in the tool but absent at the broker without a closing fill → discrepancy
        broker_isins = {p.isin for p in positions if p.isin}
        missing = [
            r.isin
            for r in s.scalars(
                select(PositionRow).where(
                    PositionRow.account_id.in_([cash_acc.id, srd_acc.id]),
                    PositionRow.closed_at.is_(None),
                    PositionRow.qty_held > 0,
                )
            )
            if r.isin not in broker_isins
        ]
        if alerts is not None:
            from app.notify.base import Event

            if missing:
                alerts.emit(
                    s,
                    Event(
                        "P3",
                        "broker_discrepancy",
                        f"{len(missing)} position(s) absente(s) chez Saxo",
                        [
                            f"{i} : détenue dans l'outil, absente chez le courtier — vente non importée ?"
                            for i in missing
                        ][:8],
                    ),
                    mode,
                    False,
                )
            if rep.suspected_duplicates or rep.unassigned_sales:
                alerts.emit(
                    s,
                    Event(
                        "P3",
                        "reconciliation",
                        "Rapprochement Saxo : écarts à documenter",
                        [
                            f"{rep.suspected_duplicates} exécution(s) probablement en double (déclaration hors tolérance)",  # noqa: E501
                            f"{rep.unassigned_sales} vente(s) sans position ouverte",
                        ],
                    ),
                    mode,
                    False,
                )
        diffs = {
            "matched": rep.matched,
            "discretionary": rep.discretionary,
            "unmatched_declared": rep.unmatched_declared,
            "suspected_duplicates": rep.suspected_duplicates,
            "unassigned_sales": rep.unassigned_sales,
            "stops_updated": stops_updated,
            "orders_open": len(orders),
            "closed_fills": len(closed),
            "missing_at_broker": missing,
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


def srd_liquidation_alerts(ctx: JobContext) -> int:
    """S7 (docs/06 §6.4): SRD positions at J−3 of the liquidation → P2 « décision de liquidation »."""
    srd = ctx.config.params.srd
    if srd is None or ctx.alerts is None:
        return 0
    from app.domain.calendar import SrdCalendar
    from app.notify.base import Button, Event

    cal = SrdCalendar.from_params(srd.calendar_2026, srd.calendar_2027_provisional)
    days_before = int((ctx.config.params.detectors or {}).get("exits", {}).get("srd_liquidation_alert_days_before", 3))
    venue = ctx.calendars.get("XPAR")
    n = 0
    with db_session() as s:
        for row in s.scalars(
            select(PositionRow).where(
                PositionRow.closed_at.is_(None), PositionRow.qty_held > 0, PositionRow.mode == "srd"
            )
        ):
            left = cal.sessions_until_liquidation(ctx.run_date, venue)
            if left > days_before:
                continue
            nxt = cal.next_liquidation(ctx.run_date)
            lines = [
                f"Liquidation le {nxt.liquidation_date:%d/%m} (règlement {nxt.settlement_date:%d/%m})"
                if nxt.settlement_date
                else f"Liquidation le {nxt.liquidation_date:%d/%m} (provisoire)",
                f"{row.qty_held} titres, PMP {row.avg_price:.2f}, stop {row.stop_current or '—'}",
                "Décider : solder / proroger (coût affiché à la proposition) / passer au comptant si cash",
                "Par défaut : ne pas proroger une position sous 1 R de gain",
            ]
            ev = Event(
                "P2",
                "srd_liquidation",
                f"{row.isin} : décision de liquidation SRD à J−{left}",
                lines,
                [Button("Vu", "vu")],
                isin=row.isin,
            )
            if ctx.alerts.emit(s, ev, ctx.mode, False, account_id=row.account_id, position_id=row.id):
                n += 1
    return n


def run(ctx: JobContext) -> int:
    import os

    if ctx.variant == "normal" and ctx.config.params.jobs.specs().get("portfolio_sync") is not None:
        srd_liquidation_alerts(ctx)

    if not (os.environ.get("SAXO_TOKEN_KEY") or os.environ.get("SAXO_ACCESS_TOKEN")):
        log.info("Saxo non configuré : synchronisation ignorée (état déclaratif)")
        return 0
    tol = ctx.config.params.accounts.cto.match_tolerance
    diffs = sync_saxo(
        SaxoPortfolio(SaxoClient()),
        MatchTolerance(float(tol.get("price_pct", 0.005)), int(tol.get("minutes", 10))),
        alerts=ctx.alerts,
        mode=ctx.mode,
    )
    return int(diffs["matched"]) + int(diffs["discretionary"])
