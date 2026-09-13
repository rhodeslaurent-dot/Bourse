"""Server-rendered pages (Jinja2 + HTMX + Tailwind CDN), mobile-first."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.routes import current_mode_payload
from app.api.security import cf_settings
from app.db.repo import freshness, last_runs, set_override
from app.db.session import db_session
from app.domain.availability import unconfirmed_slots
from app.domain.timeutil import PARIS, REUNION, format_dual, from_store
from app.scheduler.registry import IMPLEMENTED_JOBS

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["dual"] = lambda dt: format_dual(from_store(dt))


@router.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    # Phase 0: the "3 décisions du jour" page arrives in phase 1; /sante is the landing page.
    return RedirectResponse("/sante", status_code=307)


@router.get("/sante", response_class=HTMLResponse)
def sante(request: Request) -> HTMLResponse:
    st = request.app.state
    cfg = st.config
    now = datetime.now(UTC)
    with db_session() as s:
        runs = last_runs(s)
        fresh = freshness(s)
    sched = getattr(st, "scheduler", None)
    scheduled = {j.id: j.next_run_time for j in (sched.get_jobs() if sched else [])}
    jobs_view = []
    for name, spec in cfg.params.jobs.specs().items():
        run = runs.get(name)
        jobs_view.append(
            {
                "name": name,
                "implemented": name in IMPLEMENTED_JOBS,
                "enabled": spec.enabled,
                "market_days_only": spec.market_days_only,
                "next": scheduled.get(f"{name}:normal") or scheduled.get(f"{name}:premarket"),
                "last": run,
            }
        )
    av = cfg.params.availability
    unconfirmed = unconfirmed_slots(av.schedule) if av else []
    ctx = {
        "request": request,
        "now": now,
        "now_reunion": now.astimezone(REUNION),
        "now_paris": now.astimezone(PARIS),
        "config": cfg,
        "features": cfg.features,
        "jobs": jobs_view,
        "freshness": fresh,
        "mode": current_mode_payload(request),
        "unconfirmed_slots": unconfirmed,
        "cf_access": cf_settings() is not None,
        "calendars": st.calendars,
        "telegram": st.telegram_enabled,
        "email": st.email_enabled,
        "db_url_kind": st.db_kind,
    }
    return templates.TemplateResponse(request, "sante.html", ctx)


@router.post("/mode")
def set_mode_form(
    request: Request, mode: str = Form(...), duration_minutes: int | None = Form(default=None)
) -> RedirectResponse:
    from datetime import timedelta

    until = datetime.now(UTC) + timedelta(minutes=duration_minutes) if duration_minutes else None
    with db_session() as s:
        set_override(s, mode, until, "web")
    return RedirectResponse("/sante", status_code=303)
