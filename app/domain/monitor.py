"""Position monitoring (docs/06 S1, docs/07 §7.4): *threshold crossed* and *stop executed* are two
distinct states; unprotected executed quantity is a P1 in every availability mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.orders import Position


class StopEvent(StrEnum):
    NONE = "none"
    THRESHOLD_CROSSED = "threshold_crossed"  # P1 « seuil franchi — vérifier l'exécution »
    SELL_MARKET_PROPOSED = "sell_market_proposed"  # same incident, after unconfirmed_exit_minutes
    STOP_EXECUTED = "stop_executed"  # confirmed by import or declaration


@dataclass(frozen=True)
class StopCheck:
    event: StopEvent
    incident_id: str | None
    message: str


def check_stop(
    position: Position,
    position_id: int,
    last_price: float | None,
    now: datetime,
    crossed_since: datetime | None,
    exit_confirmed: bool,
    unconfirmed_exit_minutes: int,
    session_open: bool = True,
) -> StopCheck:
    """One evaluation of S1. ``crossed_since`` is the first time the threshold was seen crossed."""
    incident = f"stop_crossed:{position_id}"
    if exit_confirmed:
        return StopCheck(StopEvent.STOP_EXECUTED, incident, "stop exécuté (confirmé)")
    stop = position.stop_current
    if stop is None or last_price is None or position.qty_held == 0:
        return StopCheck(StopEvent.NONE, None, "")
    if last_price > stop:
        return StopCheck(StopEvent.NONE, None, "")
    if (
        crossed_since is not None
        and session_open
        and now - crossed_since >= timedelta(minutes=unconfirmed_exit_minutes)
    ):
        return StopCheck(
            StopEvent.SELL_MARKET_PROPOSED,
            incident,
            f"seuil {stop} franchi depuis {unconfirmed_exit_minutes} min sans exécution confirmée "
            "→ SELL au marché proposé",
        )
    return StopCheck(
        StopEvent.THRESHOLD_CROSSED,
        incident,
        f"seuil {stop} franchi (cours {last_price}) — vérifier l'exécution chez le courtier",
    )


@dataclass(frozen=True)
class UnprotectedCheck:
    incident_id: str | None
    qty_unprotected: int
    message: str


def check_unprotected(position: Position, position_id: int) -> UnprotectedCheck:
    q = position.qty_unprotected
    if q <= 0:
        return UnprotectedCheck(None, 0, "")
    return UnprotectedCheck(
        f"unprotected:{position_id}",
        q,
        f"{q} titre(s) exécuté(s) sans protection sur {position.isin} — saisir le stop chez le courtier",
    )


def is_state_fresh(synced_at: datetime | None, now: datetime, max_age_minutes: int) -> bool:
    """docs/08 §8.6: no proposal on a stale portfolio state (cash, quantities, pending orders)."""
    return synced_at is not None and now - synced_at <= timedelta(minutes=max_age_minutes)
