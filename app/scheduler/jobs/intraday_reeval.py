"""``intraday_reeval`` (every 15 s in session, user request of 28/10/2026): today's open proposals (BUY, or
WATCH waiting for a pullback) are re-evaluated with the fresh quote of the live cache — Saxo every
``live_quotes.saxo_seconds``, EODHD at its ``live_quotes.eodhd_minutes`` cadence only — through the pure
:func:`app.domain.reevaluation.classify` (admission recomputed with ``admit`` on the stored values).

Only status changes are stored (``proposal_reevaluations``); the proposal and its snapshot are never modified
(rule 10). Notifications (availability mode applied by the alert service, deduplicated per instrument, account,
kind and day): entry reached / trigger crossed when actionable with a fresh realtime quote (rule 2), degraded
configuration always (reminder to cancel a pending limit order — the tool never cancels anything, rule 9)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.dto import Quote
from app.db.models import Account, DecisionSnapshot, Proposal, ProposalReevaluation, Signal
from app.db.session import db_session
from app.domain.admission import Setup
from app.domain.letter_setup import TRIGGER_WAIT, WAITING_LIMIT
from app.domain.reevaluation import LABELS, ReevalInput, Reevaluation, classify, notify_kind, should_store
from app.domain.timeutil import format_dual, from_store
from app.notify.base import Button, Event
from app.scheduler.runner import JobContext

log = logging.getLogger("bourse.jobs.intraday_reeval")

# Setup kind of each detector (app/domain/detectors.py) — used for proposals created before ``details.configuration``
KIND_BY_DETECTOR = {
    "d2_breakout": Setup.BREAKOUT,
    "d3_range_expansion": Setup.BREAKOUT,
    "d8_buzz_precoce": Setup.BREAKOUT,
    "d4_pullback": Setup.PULLBACK,
    "d7_trend_quality": Setup.PULLBACK,
}


def candidates(s: Session, now: datetime) -> list[Proposal]:
    """Today's open proposals with a limit to post: BUY, or WATCH only because the price waits for a pullback
    (the order book's rule, docs/06 §6.7), admission tested, P_max, stop and targets stored."""
    out = []
    for p in s.scalars(
        select(Proposal)
        .where(Proposal.deleted_at.is_(None), Proposal.status == "open", Proposal.action.in_(("BUY", "WATCH")))
        .order_by(Proposal.id.desc())
        .limit(500)
    ):
        if from_store(p.created_at).date() != now.date():  # the day of the opportunities page (UTC)
            continue
        d = p.details or {}
        if d.get("admission") not in ("admis", "refuse") or not d.get("p_max") or p.stop is None or not p.targets:
            continue
        if p.expires_at is not None and from_store(p.expires_at) <= now:
            continue
        waits = [b for b in (p.gates or {}).get("blocked", []) if b.startswith("WATCH")]
        # a limit waiting for a pullback, or a letter's breakout waiting for its trigger (docs/20 §20.10)
        if any(WAITING_LIMIT not in b and TRIGGER_WAIT not in b for b in waits):
            continue
        out.append(p)
    return out


def setup_of(p: Proposal, sig: Signal | None) -> tuple[Setup | None, float | None]:
    """(setup kind, support / obstacle low) as stored by the engine; a letter's levels for older proposals."""
    conf = (p.details or {}).get("configuration") or {}
    if conf.get("type") in {k.value for k in Setup}:
        low = conf.get("niveau_bas")
        return Setup(conf["type"]), float(low) if low is not None else None
    if sig is None:
        return None, None
    if sig.detector == "external":
        lv = (sig.evidence or {}).get("niveaux") or {}
        low = lv.get("support") or lv.get("stop")
        return (Setup.PULLBACK if lv.get("support") else Setup.BREAKOUT), float(low) if low is not None else None
    return KIND_BY_DETECTOR.get(sig.detector), None


def reeval_input(p: Proposal, sig: Signal | None, price: float, q: Quote | None, now: datetime, config: Any) -> Any:
    kind, low = setup_of(p, sig)
    if kind is None:
        return None
    d = p.details or {}
    comptes = d.get("comptes") or {}
    acc = comptes.get(p.account_recommended or "") or next(
        (v for v in comptes.values() if isinstance(v, dict) and v.get("frais_aller_retour_pct") is not None), {}
    )
    k = float((getattr(config.params, "admission", None) or {}).get("min_reward_risk", 2.0))
    ent = d.get("entree") or {}
    trig = ent.get("seuil", ent.get("declencheur"))  # a breakout's plage (docs/06 §6.3); older proposals: trigger
    if (d.get("configuration") or {}).get("franchi") is False and trig is not None and price < float(trig):
        low = None  # a letter's obstacle never crossed yet: nothing to give back, the breakout is still waiting
    return ReevalInput(
        price=price,
        data_status=q.data_status.value if q is not None else "eod",
        market_timestamp=q.market_timestamp if q is not None else None,
        now=now,
        target=float(p.targets[0]) if p.targets else None,
        stop=p.stop,
        setup=kind,
        level_low=low,
        fee_fraction=float(acc.get("frais_aller_retour_pct") or 0.0),  # the stored P_max caps the result anyway
        min_reward_risk=k,
        p_max=float(d["p_max"]),
        trigger=float(trig) if trig is not None else None,
        max_market_age_minutes=int(config.params.data.buy_intraday_max_market_age_minutes),
        delay_minutes=q.declared_delay_minutes if q is not None else None,
        blocked=tuple(b for b in (p.gates or {}).get("blocked", []) if b.startswith("BLOQUÉ")),
        to_verify=bool(p.to_verify),
    )


def baseline(s: Session, p: Proposal, sig: Signal | None, config: Any, now: datetime) -> str | None:
    """Status of the proposal at creation, from its frozen snapshot price (never modified)."""
    snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
    last = ((snap.quotes or {}).get(p.isin) or {}).get("last") if snap is not None else None
    if last is None:
        return None
    inp = reeval_input(p, sig, float(last), None, now, config)
    return classify(inp).status.value if inp is not None else None


def latest(s: Session, proposal_id: int) -> ProposalReevaluation | None:
    return s.scalar(
        select(ProposalReevaluation)
        .where(ProposalReevaluation.proposal_id == proposal_id)
        .order_by(ProposalReevaluation.id.desc())
        .limit(1)
    )


def _fmt(x: float | None) -> str:
    from app.services.live_quotes import fmt_price

    return fmt_price(x)


def _event(kind: str, p: Proposal, r: Reevaluation, q: Quote, name: str) -> Event:
    from app.notify.telegram import proposal_buttons
    from app.web.labels import EXITS_NOTE, label

    d = p.details or {}
    status = {"realtime": "temps réel", "delayed": "différé", "stale": "périmé"}.get(q.data_status.value, "")
    when = f", marché {format_dual(q.market_timestamp)}" if q.market_timestamp else ""
    quote_line = f"Cours {_fmt(q.last)} ({status}{when}, {q.source})"
    if kind == "reeval_degraded":
        return Event(
            "P2",
            kind,
            f"{name} : configuration dégradée",
            [
                r.reasons[0] if r.reasons else LABELS[r.status.value],
                quote_line,
                "Ordre limite en attente chez le courtier : à revoir, l'annuler si la clôture confirme "
                "(l'outil n'annule rien)",
            ],
            [Button("Vu", f"p{p.id}:vu"), Button("Ignorer", f"p{p.id}:ignorer")],
            isin=p.isin,
        )
    acc = (d.get("comptes") or {}).get(p.account_recommended or "", {})
    ent = d.get("entree") or {}
    levels = f"limite (prix maximal) {_fmt(d.get('p_max'))} · stop {_fmt(p.stop)} · objectif {_fmt(p.targets[0])}"
    if isinstance(ent.get("seuil"), int | float):  # a breakout's plage (docs/06 §6.3): its seuil first
        levels = f"Seuil {_fmt(ent['seuil'])} · {levels}"
    lines = [quote_line, f"{levels[0].upper()}{levels[1:]} ({EXITS_NOTE})"]
    if p.account_recommended:
        # the size was computed at creation: other fills or pending orders may have used the risk budget since
        # (rule 7, max_open_risk_pct) — it is shown as an upper bound, to recompute before entering the order
        lines.append(
            f"Compte conseillé : {label('account', p.account_recommended)}"
            + (f" · au plus {acc['titres']} titre(s), taille à recalculer avant saisie" if acc.get("titres") else "")
        )
    from app.services.explain import entry_order_line

    lines += [
        entry_order_line(ent) or "Ordre limite au prix maximal, jamais au-delà",
        "Stop à poser dès l'exécution ; aucun ordre n'est passé",
    ]
    return Event("P2", kind, f"{name} : {LABELS[r.status.value]}", lines, proposal_buttons(p.id), isin=p.isin)


def reevaluate_once(s: Session, ctx: JobContext, props: list[Proposal], quotes: dict[str, Quote], now: datetime) -> int:
    from app.services.instruments import name_of

    stored = 0
    sigs = {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props])))}
    for p in props:
        q = quotes.get(p.isin)
        if q is None or q.last is None:
            continue
        sig = sigs.get(p.signal_id)
        inp = reeval_input(p, sig, float(q.last), q, now, ctx.config)
        if inp is None:
            continue
        r = classify(inp)
        prev_row = latest(s, p.id)
        prev = (prev_row.status, bool(prev_row.actionable)) if prev_row is not None else None
        if not should_store(prev, r):
            continue
        s.add(
            ProposalReevaluation(
                proposal_id=p.id,
                isin=p.isin,
                ts=now,
                price=float(q.last),
                source=q.source,
                market_timestamp=q.market_timestamp,
                data_status=q.data_status.value,
                status=r.status.value,
                actionable=r.actionable,
                reasons=r.reasons,
            )
        )
        s.flush()
        stored += 1
        kind = notify_kind(prev, baseline(s, p, sig, ctx.config, now) if prev is None else None, r)
        if kind and ctx.alerts is not None:
            acc_id = (
                s.scalar(select(Account.id).where(Account.type == p.account_recommended))
                if p.account_recommended
                else None
            )
            ctx.alerts.emit(s, _event(kind, p, r, q, name_of(p.isin)), ctx.mode, True, now, acc_id)
    return stored


def run(ctx: JobContext) -> int:
    from app.services.live_quotes import service

    now = datetime.now(UTC)
    with db_session() as s:
        props = candidates(s, now)
        if not props:
            ctx.note = "aucune proposition ouverte à réévaluer"
            return 0
        quotes = service().quotes(s, ctx.config, ctx.calendars, sorted({p.isin for p in props}), now)
        n = reevaluate_once(s, ctx, props, quotes, now)
        ctx.note = f"{len(props)} proposition(s) suivie(s), {len(quotes)} cours de séance, {n} changement(s)"
        return n
