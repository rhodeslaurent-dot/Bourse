"""``/lettres`` (docs/20 §20.6): daily review of the letters — synthesis, recommendations (Python),
validations and opinions (Claude, checked), letters of the period."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import LetterOpinion, LetterReview, LetterValidation, NewsletterItem
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.services.instruments import name_of
from app.services.letters_review import REC_LABELS, params_of, recommendations, run_review
from app.web.routes import templates

router = APIRouter()


@router.get("/lettres", response_class=HTMLResponse)
def letters_page(request: Request, day: date | None = None) -> HTMLResponse:
    st = request.app.state
    cfg = st.config
    day = day or datetime.now(PARIS).date()
    ctx: dict = {"request": request, "day": day, "enabled": params_of(cfg) is not None, "rec_labels": REC_LABELS}
    with db_session() as s:
        rev = s.scalar(select(LetterReview).where(LetterReview.day <= day).order_by(LetterReview.ts.desc()))
        ctx["review"] = rev
        ctx["recs"] = recommendations(s, cfg, day)
        ops = list(s.scalars(select(LetterOpinion).where(LetterOpinion.review_id == rev.id))) if rev else []
        ctx["opinions"] = sorted(ops, key=lambda o: ({"negative": 0, "positive": 1}.get(o.stance, 2), o.name))
        vals = list(s.scalars(select(LetterValidation).where(LetterValidation.review_id == rev.id))) if rev else []
        ctx["validations"] = [{"v": v, "name": name_of(v.isin) if v.isin else v.target_ref} for v in vals]
        since = datetime.combine(day - timedelta(days=1), datetime.min.time(), PARIS).astimezone(UTC)
        until = datetime.combine(day + timedelta(days=1), datetime.min.time(), PARIS).astimezone(UTC)
        items = [
            i
            for i in s.scalars(select(NewsletterItem).order_by(NewsletterItem.received_at.desc()).limit(200))
            if since <= (i.received_at if i.received_at.tzinfo else i.received_at.replace(tzinfo=UTC)) < until
        ]
        ctx["items"] = items
        ctx["names"] = {o.isin: name_of(o.isin) for o in ops if o.isin}
        ctx["llm_on"] = bool(getattr(st, "llm", None) and st.llm.enabled)
    return templates.TemplateResponse(request, "lettres.html", ctx)


@router.post("/lettres/analyser")
def analyse_now(request: Request) -> RedirectResponse:
    st = request.app.state
    if params_of(st.config) is None:
        return RedirectResponse("/lettres?msg=" + quote("groupe letters_review absent de params.yaml"), status_code=303)
    with db_session() as s:
        rev = run_review(s, st.config, getattr(st, "llm", None), trigger="manual", alerts=getattr(st, "alerts", None))
        msg = {
            "ok": f"Revue terminée : {len(rev.item_ids or [])} lettre(s) analysée(s)",
            "empty": "Toutes les lettres récentes sont déjà analysées",
            "rules": "IA non configurée : lettres listées sans analyse",
            "unavailable": "IA indisponible : " + (rev.synthesis or ""),
        }.get(rev.status, rev.status)
    return RedirectResponse("/lettres?msg=" + quote(msg), status_code=303)
