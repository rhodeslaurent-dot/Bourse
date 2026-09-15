"""Order → execution → protection state machine (CLAUDE.md rule 9, docs/07 §7.4, docs/09 §9.1).

Pure domain. Two independent states per position, tracked **by quantity**:
- execution: ``order_entered`` (no position yet) → ``filled_partial`` → ``filled`` → ``closed``
- protection: ``unprotected`` → ``partially_protected`` → ``protected``

A declared execution (``declared_uid``) is provisional; a broker fill (``broker_fill_id``) is
confirmed; both are *matched* (same account, same instrument, exact quantity, price ± tolerance,
± minutes) and kept on one line with both references. An order alone never creates a position.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    LIMITE = "limite"
    SEUIL = "seuil"  # à seuil de déclenchement → au marché au franchissement
    PLAGE = "plage"  # à plage de déclenchement → limite au franchissement
    LIE_IF_DONE = "lie_if_done"
    MARCHE = "marche"


class OrderState(StrEnum):
    ENTERED = "entered"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"


class ExecutionState(StrEnum):
    ORDER_ENTERED = "order_entered"
    FILLED_PARTIAL = "filled_partial"
    FILLED = "filled"
    CLOSED = "closed"


class ProtectionState(StrEnum):
    UNPROTECTED = "unprotected"
    PARTIALLY_PROTECTED = "partially_protected"
    PROTECTED = "protected"


class TradeKind(StrEnum):
    DECLARED = "declared"
    CONFIRMED = "confirmed"


class StopOrderType(StrEnum):
    SEUIL = "seuil"
    PLAGE = "plage"


class StopKind(StrEnum):
    INITIAL = "initial"
    TRAILING = "trailing"
    MANUAL = "manual"
    LINKED_IF_DONE = "linked_if_done"


class BrokerStopStatus(StrEnum):
    ACTIVE = "active"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Execution:
    account_id: int
    isin: str
    side: Side
    qty: int
    price: float
    ts: datetime
    kind: TradeKind
    declared_uid: str | None = None
    broker_fill_id: str | None = None
    currency: str = "EUR"
    fx_rate: float = 1.0
    fees: float = 0.0
    ttf: float = 0.0
    mode: str = "comptant"  # comptant | srd
    source: str = "manual"  # telegram | web | saxo_api | csv_boursobank | manual
    matched_ref: str | None = None  # the other reference once matched


def declared_uid(account_id: int, isin: str, ts: datetime, qty: int, price: float) -> str:
    """Idempotency key for manual declarations (docs/10 §10.1): same minute + qty + price → same uid."""
    key = f"{account_id}|{isin}|{ts.astimezone().strftime('%Y-%m-%dT%H:%M')}|{qty}|{price:.4f}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class MatchTolerance:
    price_pct: float = 0.005
    minutes: int = 10


def matches(declared: Execution, confirmed: Execution, tol: MatchTolerance) -> bool:
    """docs/08 §8.6: same account, same instrument, same side, exact qty, price ± tol, ± minutes."""
    if declared.kind != TradeKind.DECLARED or confirmed.kind != TradeKind.CONFIRMED:
        return False
    if (declared.account_id, declared.isin, declared.side, declared.qty) != (
        confirmed.account_id,
        confirmed.isin,
        confirmed.side,
        confirmed.qty,
    ):
        return False
    if confirmed.price <= 0 or abs(declared.price / confirmed.price - 1) > tol.price_pct:
        return False
    return abs(declared.ts - confirmed.ts) <= timedelta(minutes=tol.minutes)


def match_executions(
    declared: list[Execution], confirmed: list[Execution], tol: MatchTolerance
) -> tuple[list[tuple[Execution, Execution]], list[Execution], list[Execution]]:
    """Greedy one-to-one matching. Returns (pairs, unmatched_declared, unmatched_confirmed).

    An unmatched confirmed fill is a *discretionary?* trade to document; an unmatched declaration
    older than 24 h raises an alert (caller).
    """
    pairs: list[tuple[Execution, Execution]] = []
    used: set[int] = set()
    left_d: list[Execution] = []
    for d in declared:
        if d.matched_ref:
            continue
        best: tuple[float, int] | None = None
        for i, c in enumerate(confirmed):
            if i in used or c.matched_ref:
                continue
            if matches(d, c, tol):
                dist = abs((d.ts - c.ts).total_seconds())
                if best is None or dist < best[0]:
                    best = (dist, i)
        if best is None:
            left_d.append(d)
        else:
            used.add(best[1])
            pairs.append((d, confirmed[best[1]]))
    left_c = [c for i, c in enumerate(confirmed) if i not in used and not c.matched_ref]
    return pairs, left_d, left_c


def merge_matched(declared: Execution, confirmed: Execution) -> Execution:
    """One line, both references kept; confirmed price/qty/fees prevail (broker is the truth)."""
    return replace(
        confirmed,
        declared_uid=declared.declared_uid,
        matched_ref=declared.declared_uid,
        source=confirmed.source,
    )


@dataclass(frozen=True)
class Stop:
    level: float
    order_type: StopOrderType
    qty_covered: int
    kind: StopKind
    ts: datetime
    broker_status: BrokerStopStatus = BrokerStopStatus.UNKNOWN
    broker_confirmed_at: datetime | None = None


@dataclass
class Position:
    account_id: int
    isin: str
    qty_held: int = 0
    avg_price: float = 0.0  # PMP d'entrée, frais inclus
    qty_ordered: int = 0  # quantité de l'ordre d'entrée (pour filled_partial)
    stops: list[Stop] = field(default_factory=list)
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    mode: str = "comptant"
    stop_initial: float | None = None
    protected_at: datetime | None = None
    executions: list[Execution] = field(default_factory=list)

    # --- states ---------------------------------------------------------------------------
    @property
    def qty_protected(self) -> int:
        active = [s for s in self.stops if s.broker_status in (BrokerStopStatus.ACTIVE, BrokerStopStatus.UNKNOWN)]
        return min(self.qty_held, sum(s.qty_covered for s in active))

    @property
    def qty_unprotected(self) -> int:
        return max(0, self.qty_held - self.qty_protected)

    @property
    def stop_current(self) -> float | None:
        active = [s for s in self.stops if s.broker_status in (BrokerStopStatus.ACTIVE, BrokerStopStatus.UNKNOWN)]
        return max((s.level for s in active), default=None)

    @property
    def execution_state(self) -> ExecutionState:
        if self.closed_at is not None or (self.qty_held == 0 and self.executions):
            return ExecutionState.CLOSED
        if self.qty_held == 0:
            return ExecutionState.ORDER_ENTERED
        if self.qty_ordered and self.qty_held < self.qty_ordered:
            return ExecutionState.FILLED_PARTIAL
        return ExecutionState.FILLED

    @property
    def protection_state(self) -> ProtectionState:
        if self.qty_held == 0 or self.qty_protected == 0:
            return ProtectionState.UNPROTECTED
        if self.qty_protected < self.qty_held:
            return ProtectionState.PARTIALLY_PROTECTED
        return ProtectionState.PROTECTED

    # --- transitions ----------------------------------------------------------------------
    def apply_execution(self, ex: Execution) -> None:
        """Only a trade creates or changes a position (rule 9)."""
        if ex.side == Side.BUY:
            cost = ex.qty * ex.price * ex.fx_rate + ex.fees + ex.ttf
            total = self.qty_held * self.avg_price + cost
            self.qty_held += ex.qty
            self.avg_price = total / self.qty_held if self.qty_held else 0.0
            if self.opened_at is None:
                self.opened_at = ex.ts
        else:
            if ex.qty > self.qty_held:
                raise ValueError("vente supérieure à la quantité détenue")
            self.qty_held -= ex.qty
            if self.qty_held == 0:
                self.closed_at = ex.ts
        self.mode = ex.mode
        self.executions.append(ex)

    def apply_stop(self, stop: Stop) -> None:
        """A stop never goes down for a long position (docs/13 §13.7) except via broker status."""
        current = self.stop_current
        if current is not None and stop.level < current and stop.kind != StopKind.MANUAL:
            raise ValueError(f"stop {stop.level} < stop courant {current} : un stop ne baisse jamais")
        if self.stop_initial is None and stop.kind == StopKind.INITIAL:
            self.stop_initial = stop.level
        self.stops.append(stop)
        if self.protection_state == ProtectionState.PROTECTED and self.protected_at is None:
            self.protected_at = stop.ts

    def stale_stops(self) -> list[Stop]:
        """Stops covering more than held (after a partial sale) — to be resubmitted (docs/07 §7.4)."""
        return [s for s in self.stops if s.broker_status == BrokerStopStatus.ACTIVE and s.qty_covered > self.qty_held]


def order_state(
    qty_ordered: int, qty_filled: int, cancelled: bool = False, expired: bool = False, rejected: bool = False
) -> OrderState:
    if rejected:
        return OrderState.REJECTED
    if qty_filled >= qty_ordered > 0:
        return OrderState.FILLED
    if cancelled:
        return OrderState.CANCELLED
    if expired:
        return OrderState.EXPIRED
    if qty_filled > 0:
        return OrderState.PARTIALLY_FILLED
    return OrderState.ENTERED
