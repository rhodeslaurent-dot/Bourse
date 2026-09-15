"""Declarations API (docs/09 §9.6): ``POST /api/orders``, ``POST /api/executions`` (idempotent),
``POST /api/protections``, ``GET /api/positions``. Declarations only — never an order to a broker."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Account, Order, PortfolioState, PositionRow
from app.db.session import db_session
from app.services.portfolio import account_by_type, declare_execution, declare_order, declare_stop, ensure_accounts

router = APIRouter(prefix="/api")

ACCOUNT_TYPES = ("pea", "cto_cash", "cto_srd")


class OrderIn(BaseModel):
    account: str = Field(pattern="^(pea|cto_cash|cto_srd)$")
    isin: str = Field(min_length=12, max_length=12)
    side: str = Field(pattern="^(buy|sell)$")
    qty: int = Field(gt=0)
    order_type: str = Field(pattern="^(limite|seuil|plage|lie_if_done|marche)$")
    limit_price: float | None = None
    trigger_price: float | None = None
    validity: str | None = "day"
    proposal_id: int | None = None


class ExecutionIn(BaseModel):
    account: str = Field(pattern="^(pea|cto_cash|cto_srd)$")
    isin: str = Field(min_length=12, max_length=12)
    side: str = Field(pattern="^(buy|sell)$")
    qty: int = Field(gt=0)
    price: float = Field(gt=0)
    ts: datetime | None = None
    order_id: int | None = None
    proposal_id: int | None = None
    fees: float = 0.0
    currency: str = "EUR"
    fx_rate: float = 1.0


class ProtectionIn(BaseModel):
    position_id: int
    level: float = Field(gt=0)
    order_type: str = Field(pattern="^(seuil|plage)$")
    qty_covered: int = Field(gt=0)
    kind: str = Field(default="initial", pattern="^(initial|trailing|manual|linked_if_done)$")


def _source(request: Request) -> str:
    return "api" if getattr(request.state, "auth", "") == "app_token" else "web"


@router.post("/orders")
def post_order(body: OrderIn, request: Request) -> dict[str, object]:
    with db_session() as s:
        acc = account_by_type(s, body.account)
        o = declare_order(
            s,
            acc.id,
            body.isin.upper(),
            body.side,
            body.qty,
            body.order_type,
            body.limit_price,
            body.trigger_price,
            body.validity,
            _source(request),
            body.proposal_id,
        )
        return {"order_id": o.id, "state": o.state, "position_created": False}


@router.post("/executions")
def post_execution(body: ExecutionIn, request: Request) -> dict[str, object]:
    with db_session() as s:
        acc = account_by_type(s, body.account)
        r = declare_execution(
            s,
            acc.id,
            body.isin.upper(),
            body.side,
            body.qty,
            body.price,
            body.ts or datetime.now(UTC),
            _source(request),
            mode="srd" if body.account == "cto_srd" else "comptant",
            order_id=body.order_id,
            proposal_id=body.proposal_id,
            fees=body.fees,
            currency=body.currency,
            fx_rate=body.fx_rate,
        )
        pos = s.get(PositionRow, r.position_id) if r.position_id else None
        return {
            "created": r.created,
            "trade_id": r.trade_id,
            "position_id": r.position_id,
            "message": r.message,
            "execution_state": pos.execution_state if pos else None,
            "protection_state": pos.protection_state if pos else None,
        }


@router.post("/protections")
def post_protection(body: ProtectionIn, request: Request) -> dict[str, object]:
    with db_session() as s:
        try:
            p = declare_stop(
                s, body.position_id, body.level, body.order_type, body.qty_covered, body.kind, _source(request)
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "position_id": body.position_id,
            "protection_state": p.protection_state.value,
            "qty_protected": p.qty_protected,
            "stop_current": p.stop_current,
        }


@router.get("/positions")
def get_positions(request: Request) -> dict[str, object]:
    max_age = request.app.state.config.params.accounts.portfolio_state_max_age_minutes
    with db_session() as s:
        ensure_accounts(s)
        accounts = {a.id: a for a in s.scalars(select(Account))}
        states = {st.account_id: st for st in s.scalars(select(PortfolioState))}
        rows = []
        now = datetime.now(UTC)
        for r in s.scalars(
            select(PositionRow)
            .where(PositionRow.closed_at.is_(None))
            .order_by(PositionRow.account_id, PositionRow.isin)
        ):
            st = states.get(r.account_id)
            synced = (
                st.synced_at.replace(tzinfo=UTC)
                if st and st.synced_at and st.synced_at.tzinfo is None
                else (st.synced_at if st else None)
            )
            rows.append(
                {
                    "position_id": r.id,
                    "account": accounts[r.account_id].type,
                    "isin": r.isin,
                    "qty_held": r.qty_held,
                    "qty_protected": r.qty_protected,
                    "qty_unprotected": max(0, r.qty_held - r.qty_protected),
                    "avg_price": r.avg_price,
                    "execution_state": r.execution_state,
                    "protection_state": r.protection_state,
                    "stop_current": r.stop_current,
                    "stop_initial": r.stop_initial,
                    "mode": r.mode,
                    "last_price": r.last_price,
                    "last_price_status": r.last_price_status or "indisponible",
                    "state_age_minutes": round((now - synced).total_seconds() / 60, 1) if synced else None,
                    "state_fresh": bool(synced and (now - synced).total_seconds() <= max_age * 60),
                    "state_declarative": st.declarative if st else True,
                }
            )
        orders = [
            {
                "order_id": o.id,
                "isin": o.isin,
                "side": o.side,
                "qty": o.qty,
                "qty_filled": o.qty_filled,
                "state": o.state,
                "order_type": o.order_type,
            }
            for o in s.scalars(select(Order).where(Order.state.in_(("entered", "partially_filled"))))
        ]
        cash = {
            accounts[a].type: {
                "cash": accounts[a].cash,
                "updated_at": accounts[a].cash_updated_at.isoformat() if accounts[a].cash_updated_at else None,
                "source": accounts[a].cash_source,
            }
            for a in accounts
        }
    return {"positions": rows, "open_orders": orders, "cash": cash}
