"""``/fonctionnement`` (flow diagram with the real last run of each feature) and ``/aide`` (how to read the
pages, glossary, how to set up the tool and reconnect its links) — user request of 26/10/2026."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db.session import db_session
from app.services.flow import flow_state
from app.web.routes import templates

router = APIRouter()


@router.get("/fonctionnement", response_class=HTMLResponse)
def flow_page(request: Request) -> HTMLResponse:
    now = datetime.now(UTC)
    with db_session() as s:
        stages = flow_state(s, now)
    return templates.TemplateResponse(
        request, "fonctionnement.html", {"request": request, "stages": stages, "now": now}
    )


@router.get("/aide", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "aide.html", {"request": request})
