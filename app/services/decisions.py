"""Proposals, decisions and frozen snapshots (CLAUDE.md rule 10, docs/13 §13.3). Phase 1 records
the minimum for every proposal — even a newsletter-only signal — so that the three-approach
comparison (docs/12) can be computed later from the snapshot, never from corrected data."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Decision,
    DecisionSnapshot,
    FeatureDaily,
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
    config: Any | None = None,
    created_at: datetime | None = None,
    as_of: date | None = None,
    demote: bool = True,
    **fields: Any,
) -> Proposal:
    """Create the proposal and its snapshot in one go: the snapshot is what the decision saw.

    With ``config`` and ``learning`` enabled, the comparable history (docs/18 §18.5) is attached to
    the snapshot and an unfavourable BUY on enough cases is issued as WATCH (docs/12 §12.5.3).

    ``created_at`` / ``as_of`` reconstitute a past proposal (docs/19): the snapshot then holds only
    the indicators and regime of the last stored day on or before ``as_of`` and no portfolio state.

    ``demote=False`` (graduated verdict, docs/37 L0): the history is attached to the snapshot but never demotes — the
    engine already made an unfavourable history an element of appreciation of a speculative card."""
    track = None
    if config is not None:
        from app.domain.learning import should_demote
        from app.services.outcomes import comparable_history, learning_params

        track = comparable_history(s, config, sig, datetime.now(UTC))
        lp = learning_params(config)
        min_signals = int((config.params.review or {}).get("profitability_min_signals_per_detector", 60))
        if (
            demote
            and track is not None
            and lp is not None
            and should_demote(action, track, min_signals, lp.demote_buy_to_watch)
        ):
            action = "WATCH"
            gates = dict(gates or {})
            gates["blocked"] = [
                *(gates.get("blocked") or []),
                f"BLOQUÉ : historique défavorable ({track.n} cas comparables, espérance rétrécie "
                f"{track.shrunk_r:+.2f} R) → WATCH seulement (learning.demote_buy_to_watch)",
            ]
    p = Proposal(
        signal_id=sig.id,
        isin=sig.isin,
        action=action,
        gates=_jsonable(gates or {}),
        to_verify=to_verify,
        status="open" if action in ("BUY", "WATCH") else "open",
        created_at=created_at or datetime.now(UTC),
        expires_at=expires_at,
        **fields,
    )
    s.add(p)
    s.flush()
    if as_of is None:
        feats = latest_features(s, [sig.isin]).get(sig.isin)
        regime = s.scalar(
            select(MarketRegimeRow).where(MarketRegimeRow.market == "EU").order_by(MarketRegimeRow.id.desc())
        )
        states = [
            {"account_id": st.account_id, "cash": st.cash, "synced_at": st.synced_at, "declarative": st.declarative}
            for st in s.scalars(select(PortfolioState))
        ]
    else:  # no data after as_of may enter a reconstituted snapshot (rule 10)
        feats = s.scalar(
            select(FeatureDaily)
            .where(FeatureDaily.isin == sig.isin, FeatureDaily.day <= as_of)
            .order_by(FeatureDaily.day.desc())
        )
        regime = s.scalar(
            select(MarketRegimeRow)
            .where(MarketRegimeRow.market == "EU", MarketRegimeRow.day <= as_of)
            .order_by(MarketRegimeRow.day.desc())
        )
        states = []
    s.add(
        DecisionSnapshot(
            proposal_id=p.id,
            taken_at=p.created_at,
            quotes=_jsonable(quotes or {}),
            features=_jsonable(
                {
                    **(
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
                        if feats
                        else {}
                    ),
                    **({"track_record": track.as_dict()} if track is not None else {}),
                }
            )
            or None,
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
