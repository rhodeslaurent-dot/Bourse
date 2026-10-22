"""``/rapport`` (docs/09 §9.2: report of the day in 10 rubrics, archived) and ``/revue`` (docs/22
§22.5: portfolio review, weekly or on demand)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import DailyReport, PortfolioReview
from app.db.session import db_session
from app.services.report import build, store, today_paris
from app.web.routes import templates

router = APIRouter()


@router.get("/rapport", response_class=HTMLResponse)
def report_page(request: Request, day: date | None = None) -> HTMLResponse:
    cfg = request.app.state.config
    day = day or today_paris()
    with db_session() as s:
        row = s.scalar(select(DailyReport).where(DailyReport.day == day))
        archived = row is not None
        content = row.content if row is not None else build(s, cfg, day)
        days = [d for d in s.scalars(select(DailyReport.day).order_by(DailyReport.day.desc()).limit(20))]
    return templates.TemplateResponse(
        request, "rapport.html", {"request": request, "c": content, "day": day, "archived": archived, "days": days}
    )


@router.post("/rapport/archiver")
def archive_report(request: Request) -> RedirectResponse:
    day = today_paris()
    with db_session() as s:
        store(s, day, build(s, request.app.state.config, day))
    return RedirectResponse(f"/rapport?day={day.isoformat()}", status_code=303)


@router.get("/revue", response_class=HTMLResponse)
def review_page(request: Request, rid: int | None = None) -> HTMLResponse:
    with db_session() as s:
        q = select(PortfolioReview)
        rev = s.get(PortfolioReview, rid) if rid else s.scalar(q.order_by(PortfolioReview.id.desc()))
        history = list(s.scalars(select(PortfolioReview).order_by(PortfolioReview.id.desc()).limit(12)))
        data = (
            {
                "id": rev.id,
                "ts": rev.ts,
                "day": rev.day,
                "trigger": rev.trigger,
                "lines": rev.lines or [],
                "syn": rev.synthesis or {},
            }
            if rev
            else None
        )
        hist = [{"id": h.id, "day": h.day, "trigger": h.trigger} for h in history]
    return templates.TemplateResponse(request, "revue.html", {"request": request, "rev": data, "history": hist})


@router.post("/revue/lancer")
def run_review_now(request: Request) -> RedirectResponse:
    from app.services.portfolio_review import run_review

    with db_session() as s:
        rev = run_review(s, request.app.state.config, trigger="manual")
        rid = rev.id
    return RedirectResponse(f"/revue?rid={rid}", status_code=303)


@router.get("/rotation", response_class=HTMLResponse)
def rotation_page(request: Request) -> HTMLResponse:
    """docs/22 §22.4 C — sector strength (median rank of their stocks) and ETF ranking."""
    from app.services.rotation import etf_ranking, sector_strength

    day = today_paris()
    with db_session() as s:
        sectors = sector_strength(s, day)
        etfs = etf_ranking(s, day, 40)
    return templates.TemplateResponse(
        request, "rotation.html", {"request": request, "sectors": sectors, "etfs": etfs, "day": day}
    )
