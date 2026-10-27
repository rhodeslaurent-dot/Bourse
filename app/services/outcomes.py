"""Outcomes of past proposals and the samples built from them (docs/18 §18.4–18.5).

Data access only; the computation lives in ``app/domain/outcomes.py`` and ``app/domain/learning.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    Decision,
    DecisionSnapshot,
    Instrument,
    Order,
    PriceEod,
    Proposal,
    Signal,
    SignalOutcome,
    Trade,
)
from app.domain.calendar import MarketCalendars, VenueCalendar
from app.domain.learning import Sample, TrackRecord, track_record
from app.domain.outcomes import DayBar, OutcomeInput, evaluate
from app.domain.timeutil import PARIS

CLOSE_PARIS = time(17, 40)


def learning_params(config: LoadedConfig) -> Any | None:
    return config.params.learning if config.feature_enabled("learning") else None


def decision_group(s: Session, p: Proposal) -> str:
    decisions = {d.decision for d in s.scalars(select(Decision).where(Decision.proposal_id == p.id))}
    linked = s.scalar(select(Order.id).where(Order.proposal_id == p.id)) or s.scalar(
        select(Trade.id).where(Trade.proposal_id == p.id)
    )
    if "order_entered" in decisions or linked:
        return "prise"
    if "ignored" in decisions:
        return "ignoree"
    if decisions & {"seen", "watch", "snoozed"}:
        return "vue_sans_suite"
    return "sans_decision"


def entry_limit(p: Proposal, sig: Signal | None) -> float | None:
    ez = p.entry_zone or {}
    for k in ("max", "high", "entry_high", "achat", "low", "entry_low"):
        v = ez.get(k)
        if isinstance(v, int | float) and v > 0:
            return float(v)
    if sig is not None:
        return sig.entry_high or sig.entry_low
    return None


def _cost_per_share(p: Proposal) -> float | None:
    ce = p.cost_estimate or {}
    total = ce.get("roundtrip_eur") or ce.get("total_eur")
    return float(total) / p.shares if total and p.shares else None


def _paris(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(PARIS)


def _venue(s: Session, calendars: MarketCalendars, isin: str) -> VenueCalendar:
    inst = s.get(Instrument, isin)
    mic = inst.mic if inst and inst.mic in calendars.venues else "XPAR"
    return calendars.get(mic)


def _before_open(cal: VenueCalendar, created_at: datetime) -> bool:
    local = (created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)).astimezone(cal.tz)
    info = cal.session(local.date())
    return bool(info.is_open and info.open_time and local.time().replace(tzinfo=None) < info.open_time)


def last_expected_session(cal: VenueCalendar, now: datetime) -> date:
    """Latest session whose *official* close should be stored: the previous session (official
    closes are loaded the next morning by ``eod_backfill_check``)."""
    return cal.previous_session(now.astimezone(PARIS).date())


def outcome_for(
    s: Session, config: LoadedConfig, calendars: MarketCalendars, p: Proposal, now: datetime
) -> SignalOutcome:
    lp = config.params.learning
    sig = s.get(Signal, p.signal_id)
    cal = _venue(s, calendars, p.isin)
    created = _paris(p.created_at)
    upto = last_expected_session(cal, now)
    sessions = [created.date(), *cal.sessions_between(created.date() + timedelta(days=1), upto)]
    first = sessions[1] if len(sessions) > 1 else created.date()
    rows = s.scalars(
        select(PriceEod).where(PriceEod.isin == p.isin, PriceEod.day >= created.date(), PriceEod.day <= upto)
    )
    bars = {r.day: DayBar(r.day, r.open, r.high, r.low, r.close) for r in rows if r.official}
    targets = p.targets or (sig.targets if sig else None) or []
    inp = OutcomeInput(
        created_day=created.date(),
        created_before_open=_before_open(cal, p.created_at),
        entry_price=entry_limit(p, sig),
        stop=p.stop or (sig.stop_initial if sig else None),
        target=float(targets[0]) if targets and isinstance(targets[0], int | float) else None,
        slippage_pct=config.params.risk.slippage_pct,
        validity_sessions=lp.entry_validity_sessions if lp else 5,
        horizon_sessions=lp.horizon_sessions if lp else 20,
        expires_day=_paris(p.expires_at).date() if p.expires_at else None,
        cost_per_share=_cost_per_share(p),
    )
    o = evaluate(inp, sessions, bars)
    row = s.scalar(select(SignalOutcome).where(SignalOutcome.proposal_id == p.id)) or SignalOutcome(
        proposal_id=p.id, signal_id=p.signal_id, isin=p.isin
    )
    row.computed_at = now
    with s.no_autoflush:
        row.decision_group = decision_group(s, p)
    for k in (
        "status",
        "final",
        "ref_price",
        "entry_day",
        "entry_price",
        "exit_day",
        "exit_price",
        "exit_reason",
        "r_gross",
        "r_net",
        "costs_counted",
        "ret_pct",
        "mfe_pct",
        "mae_pct",
        "mfe_r",
        "mae_r",
        "ret_1d",
        "ret_5d",
        "ret_20d",
        "last_bar_day",
        "note",
        "ghost_ref_price",
        "ghost_ret_pct",
        "ghost_gap_pct",
    ):
        setattr(row, k, getattr(o, k))
    if o.status == "pending" and first:
        row.note = f"première séance évaluée : {first:%d/%m/%Y}"
    if row.id is None:
        s.add(row)
    return row


def update_outcomes(s: Session, config: LoadedConfig, calendars: MarketCalendars, now: datetime | None = None) -> int:
    """(Re)compute every proposal whose outcome is missing or not final. Final outcomes are kept,
    only their decision group is refreshed (a late decision is still recorded)."""
    now = now or datetime.now(UTC)
    finals = {o.proposal_id: o for o in s.scalars(select(SignalOutcome).where(SignalOutcome.final.is_(True)))}
    n = 0
    for p in s.scalars(select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id)):
        if p.id in finals:
            finals[p.id].decision_group = decision_group(s, p)
            continue
        try:
            outcome_for(s, config, calendars, p, now)
        except ValueError as exc:  # unknown calendar year: never guess the sessions
            row = s.scalar(select(SignalOutcome).where(SignalOutcome.proposal_id == p.id))
            if row is None:
                row = SignalOutcome(
                    proposal_id=p.id, signal_id=p.signal_id, isin=p.isin, decision_group="sans_decision"
                )
                s.add(row)
            row.computed_at, row.status, row.final, row.note = now, "unavailable", False, str(exc)
        n += 1
    s.flush()
    return n


def _final_at(o: SignalOutcome) -> datetime | None:
    day = o.exit_day or o.last_bar_day
    return datetime.combine(day, CLOSE_PARIS, PARIS).astimezone(UTC) if day and o.final else None


def samples(s: Session) -> list[Sample]:
    """Final outcomes with grouping keys from the proposal, its signal and its frozen snapshot."""
    out: list[Sample] = []
    rows = list(s.scalars(select(SignalOutcome).where(SignalOutcome.final.is_(True))))
    for o in rows:
        p = s.get(Proposal, o.proposal_id)
        if p is None or p.deleted_at is not None:
            continue
        sig = s.get(Signal, p.signal_id)
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
        feats = (snap.features if snap else None) or {}
        ev = (sig.evidence if sig else None) or {}
        hour = _paris(p.created_at).hour
        out.append(
            Sample(
                proposal_id=p.id,
                created_at=p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=UTC),
                final_at=_final_at(o),
                r=o.r_net if o.r_net is not None else o.r_gross,
                ret_pct=o.ret_pct,
                keys={
                    "detector": sig.detector if sig else None,
                    "source": sig.source if sig else None,
                    "action": p.action,
                    "regime": ((snap.regime or {}).get("regime") if snap else None),
                    "account": p.account_recommended,
                    "decision_group": o.decision_group,
                    "hour": f"{hour:02d}h",
                },
                values={
                    "rs_rank": feats.get("rs_rank"),
                    "drift": ev.get("drift_since_open"),
                    "gap": ev.get("gap_open"),
                },
            )
        )
    return out


def comparable_history(s: Session, config: LoadedConfig, sig: Signal, at: datetime) -> TrackRecord | None:
    """Track record a new proposal sees at its creation (docs/18 §18.5); None if learning is off."""
    lp = learning_params(config)
    if lp is None:
        return None
    return track_record(
        samples(s),
        at,
        sig.detector,
        sig.source,
        k=lp.shrinkage_k,
        min_n=lp.min_samples_adjust,
        favorable=lp.favorable_r,
        unfavorable=lp.unfavorable_r,
    )
