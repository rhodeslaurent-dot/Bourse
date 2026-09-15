"""Ingestion and decision API (docs/09 §9.6): ``POST /api/ingest/rss``, ``POST /api/ingest/newsletter``
(n8n or manual), ``POST /api/decisions``, ``GET /api/opportunities?date=``, ``GET /api/proposals/{id}``."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.data.providers.imap_gmail import NewsletterMail, parse_eml
from app.data.providers.rss import parse_feed
from app.db.models import Decision, DecisionSnapshot, Proposal, Signal
from app.db.session import db_session
from app.services.decisions import record_decision
from app.services.news import ingest_rss_items
from app.services.newsletters import ingest_mail

router = APIRouter(prefix="/api")


class RssIn(BaseModel):
    source: str = Field(min_length=2, max_length=32)
    content: str  # raw feed XML


class NewsletterIn(BaseModel):
    eml_base64: str | None = None
    source: str | None = None
    subject: str | None = None
    sender: str | None = None
    body_text: str | None = None
    received_at: datetime | None = None


class DecisionIn(BaseModel):
    proposal_id: int
    decision: str = Field(pattern="^(seen|watch|order_entered|ignored|snoozed)$")
    reason: str | None = None


def _via(request: Request) -> str:
    return "api" if getattr(request.state, "auth", "") == "app_token" else "web"


@router.post("/ingest/rss")
def ingest_rss(body: RssIn, request: Request) -> dict[str, object]:
    try:
        items = parse_feed(body.source, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with db_session() as s:
        rep = ingest_rss_items(s, items, request.app.state.llm)
    return rep.__dict__


@router.post("/ingest/newsletter")
def ingest_newsletter(body: NewsletterIn, request: Request) -> dict[str, object]:
    cfg = request.app.state.config
    senders = {k: list(v) for k, v in ((cfg.params.data.imap or {}).get("senders") or {}).items()}
    received = body.received_at or datetime.now(UTC)
    if body.eml_base64:
        mail = parse_eml(base64.b64decode(body.eml_base64), senders, received)
        if mail is None:
            raise HTTPException(400, "expéditeur non reconnu (data.imap.senders)")
    else:
        if not (body.source and body.body_text):
            raise HTTPException(400, "source et body_text requis sans eml_base64")
        import hashlib

        mid = f"manual-{hashlib.sha256((body.subject or '' + body.body_text).encode()).hexdigest()[:16]}"
        mail = NewsletterMail(
            body.source,
            mid,
            body.subject or "",
            body.sender or "",
            received,
            received,
            body.body_text,
            hashlib.sha256(f"{body.source}|{mid}".encode()).hexdigest(),
        )
    with db_session() as s:
        rep = ingest_mail(s, mail, cfg, request.app.state.llm)
    return rep.__dict__


@router.post("/decisions")
def post_decision(body: DecisionIn, request: Request) -> dict[str, object]:
    with db_session() as s:
        try:
            d = record_decision(s, body.proposal_id, body.decision, _via(request), body.reason)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "decision_id": d.id,
            "proposal_id": body.proposal_id,
            "status": s.get(Proposal, body.proposal_id).status,
        }


@router.get("/opportunities")
def opportunities(day: date | None = None) -> dict[str, object]:
    day = day or datetime.now(UTC).date()
    with db_session() as s:
        rows = []
        for p in s.scalars(
            select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id.desc()).limit(200)
        ):
            created = p.created_at.replace(tzinfo=UTC) if p.created_at.tzinfo is None else p.created_at
            if created.date() != day:
                continue
            sig = s.get(Signal, p.signal_id)
            rows.append(
                {
                    "proposal_id": p.id,
                    "isin": p.isin,
                    "action": p.action,
                    "status": p.status,
                    "to_verify": p.to_verify,
                    "detector": sig.detector if sig else None,
                    "source": sig.source if sig else None,
                    "stop": p.stop,
                    "targets": p.targets,
                    "gates": p.gates,
                    "created_at": created.isoformat(),
                }
            )
    return {"day": day.isoformat(), "proposals": rows}


@router.get("/proposals/{proposal_id}")
def proposal_detail(proposal_id: int) -> dict[str, object]:
    with db_session() as s:
        p = s.get(Proposal, proposal_id)
        if p is None:
            raise HTTPException(404, "proposition inconnue")
        sig = s.get(Signal, p.signal_id)
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
        decisions = [
            {"decision": d.decision, "ts": d.ts.isoformat(), "via": d.via, "reason": d.reason}
            for d in s.scalars(select(Decision).where(Decision.proposal_id == p.id).order_by(Decision.id))
        ]
        return {
            "proposal": {
                "id": p.id,
                "isin": p.isin,
                "action": p.action,
                "status": p.status,
                "to_verify": p.to_verify,
                "stop": p.stop,
                "targets": p.targets,
                "entry_zone": p.entry_zone,
                "gates": p.gates,
            },
            "signal": {
                "detector": sig.detector,
                "source": sig.source,
                "ts": sig.ts.isoformat(),
                "evidence": sig.evidence,
                "data_status": sig.data_status,
                "params_version": sig.params_version,
            }
            if sig
            else None,
            "snapshot": {
                "taken_at": snap.taken_at.isoformat(),
                "quotes": snap.quotes,
                "features": snap.features,
                "regime": snap.regime,
                "portfolio_state": snap.portfolio_state,
                "params_version": snap.params_version,
            }
            if snap
            else None,
            "decisions": decisions,
        }
