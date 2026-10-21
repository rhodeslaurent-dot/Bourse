"""``/opportunites`` (docs/17 §17.3): proposals of a day with a one-line explanation, an expandable
detail (fiche, data used, gates, decisions, note) and decision buttons → ``record_decision``."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import Decision, DecisionSnapshot, Proposal, Signal
from app.db.session import db_session
from app.domain.learning import VERDICT_ORDER
from app.services.decisions import record_decision
from app.services.explain import explain_proposal
from app.services.instruments import name_of
from app.services.notes import notes_for
from app.web.routes import templates

router = APIRouter()


@router.get("/opportunites", response_class=HTMLResponse)
def opportunities_page(request: Request, day: date | None = None, action: str | None = None) -> HTMLResponse:
    cfg = request.app.state.config
    capital = float(cfg.params.capital.capital_pilote_eur) or None
    day = day or datetime.now(UTC).date()
    with db_session() as s:
        props = [
            p
            for p in s.scalars(
                select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id.desc()).limit(300)
            )
            if (p.created_at.replace(tzinfo=UTC) if p.created_at.tzinfo is None else p.created_at).date() == day
            and (not action or p.action == action)
        ]
        sigs = (
            {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props])))}
            if props
            else {}
        )
        snaps = (
            {
                x.proposal_id: x
                for x in s.scalars(
                    select(DecisionSnapshot).where(DecisionSnapshot.proposal_id.in_([p.id for p in props]))
                )
            }
            if props
            else {}
        )
        decisions: dict[int, list[Decision]] = {}
        for d in (
            s.scalars(select(Decision).where(Decision.proposal_id.in_([p.id for p in props])).order_by(Decision.id))
            if props
            else []
        ):
            decisions.setdefault(d.proposal_id, []).append(d)
        letters: dict[int, dict[str, int]] = {}
        if props:
            from app.db.models import LetterValidation

            for v in s.scalars(
                select(LetterValidation).where(
                    LetterValidation.target_type == "proposal",
                    LetterValidation.target_ref.in_([str(p.id) for p in props]),
                )
            ):
                d = letters.setdefault(int(v.target_ref), {"confirme": 0, "contredit": 0})
                if v.verdict in d:
                    d[v.verdict] += 1
        notes = {
            k: {"text": v.text, "updated_at": v.updated_at}
            for k, v in notes_for(s, "proposal", [str(p.id) for p in props]).items()
        }
        items = []
        for p in props:
            sig = sigs.get(p.signal_id)
            items.append(
                {
                    "p": p,
                    "sig": sig,
                    "name": name_of(p.isin),
                    "explain": explain_proposal(p, sig, snaps.get(p.id), capital),
                    "decisions": decisions.get(p.id, []),
                    "note": notes.get(str(p.id)),
                    "letters": letters.get(p.id, {}),
                }
            )
        # docs/18 §18.5: at equal action, favourable history first, unfavourable last (stable otherwise)
        action_rank = {"BUY": 0, "WATCH": 1}
        items.sort(
            key=lambda it: (
                action_rank.get(it["p"].action, 2),
                VERDICT_ORDER.get((it["explain"].track or {}).get("verdict", "insuffisant"), 1),
            )
        )
        counts = {a: sum(1 for it in items if it["p"].action == a) for a in ("BUY", "WATCH")}
    return templates.TemplateResponse(
        request,
        "opportunites.html",
        {
            "request": request,
            "items": items,
            "day": day,
            "action": action,
            "counts": counts,
            "today": datetime.now(UTC).date(),
        },
    )


@router.post("/opportunites/decision")
def decide(
    proposal_id: int = Form(...), decision: str = Form(...), reason: str = Form(""), day: str = Form("")
) -> RedirectResponse:
    if decision not in ("seen", "watch", "order_entered", "ignored", "snoozed"):
        raise HTTPException(400, "décision inconnue")
    with db_session() as s:
        try:
            record_decision(s, proposal_id, decision, "web", reason.strip() or None)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/opportunites{'?day=' + day if day else ''}", status_code=303)
