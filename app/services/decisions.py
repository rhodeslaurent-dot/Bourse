"""Proposals, decisions and frozen snapshots (CLAUDE.md rule 10, docs/13 §13.3). Phase 1 records
the minimum for every proposal — even a newsletter-only signal — so that the three-approach
comparison (docs/12) can be computed later from the snapshot, never from corrected data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Decision,
    DecisionSnapshot,
    MarketRegimeRow,
    PortfolioState,
    Proposal,
    Signal,
    UniverseSnapshot,
)
from app.services.market_data import latest_features


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def record_signal(
    s: Session,
    isin: str,
    detector: str,
    source: str,
    ts: datetime,
    timeframe: str,
    data_status: str,
    params_version: str,
    evidence: dict | None = None,
    external_refs: list | None = None,
    **levels: Any,
) -> Signal:
    snap = s.scalar(select(UniverseSnapshot).order_by(UniverseSnapshot.id.desc()))
    sig = Signal(
        isin=isin,
        detector=detector,
        source=source,
        ts=ts,
        timeframe=timeframe,
        data_status=data_status,
        params_version=params_version,
        evidence=_jsonable(evidence or {}),
        external_refs=external_refs,
        universe_snapshot_id=snap.id if snap else None,
        entry_low=levels.get("entry_low"),
        entry_high=levels.get("entry_high"),
        stop_initial=levels.get("stop_initial"),
        targets=levels.get("targets"),
        horizon=levels.get("horizon"),
    )
    s.add(sig)
    s.flush()
    return sig


def create_proposal(
    s: Session,
    sig: Signal,
    action: str,
    quotes: dict | None,
    gates: dict | None,
    params_version: str,
    to_verify: bool = False,
    expires_at: datetime | None = None,
    **fields: Any,
) -> Proposal:
    """Create the proposal and its snapshot in one go: the snapshot is what the decision saw."""
    p = Proposal(
        signal_id=sig.id,
        isin=sig.isin,
        action=action,
        gates=_jsonable(gates or {}),
        to_verify=to_verify,
        status="open" if action in ("BUY", "WATCH") else "open",
        created_at=datetime.now(UTC),
        expires_at=expires_at,
        **fields,
    )
    s.add(p)
    s.flush()
    feats = latest_features(s, [sig.isin]).get(sig.isin)
    regime = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
    states = [
        {"account_id": st.account_id, "cash": st.cash, "synced_at": st.synced_at, "declarative": st.declarative}
        for st in s.scalars(select(PortfolioState))
    ]
    s.add(
        DecisionSnapshot(
            proposal_id=p.id,
            taken_at=p.created_at,
            quotes=_jsonable(quotes or {}),
            features=_jsonable(
                {
                    k: getattr(feats, k)
                    for k in (
                        "day",
                        "atr14",
                        "adr20",
                        "mm20",
                        "mm50",
                        "mm200",
                        "rvol",
                        "rs_rank",
                        "pivot_60",
                        "high_52w",
                        "rsi14",
                        "gap_in_data",
                    )
                }
            )
            if feats
            else None,
            regime=_jsonable({"day": regime.day, "regime": regime.regime, "details": regime.details})
            if regime
            else None,
            portfolio_state=_jsonable(states),
            params_version=params_version,
        )
    )
    s.flush()
    return p


def record_decision(s: Session, proposal_id: int, decision: str, via: str, reason: str | None = None) -> Decision:
    p = s.get(Proposal, proposal_id)
    if p is None:
        raise ValueError("proposition inconnue")
    d = Decision(proposal_id=proposal_id, decision=decision, via=via, reason=reason, ts=datetime.now(UTC))
    s.add(d)
    if decision == "ignored":
        p.status = "ignored"
    elif decision == "watch":
        p.status = "watch"
    elif decision == "order_entered":
        p.status = "taken"
    s.flush()
    return d


def expire_proposals(s: Session, now: datetime | None = None) -> list[Proposal]:
    now = now or datetime.now(UTC)
    out = []
    for p in s.scalars(select(Proposal).where(Proposal.status == "open", Proposal.expires_at.is_not(None))):
        exp = p.expires_at.replace(tzinfo=UTC) if p.expires_at.tzinfo is None else p.expires_at
        if exp <= now:
            p.status = "expired"
            out.append(p)
    return out
