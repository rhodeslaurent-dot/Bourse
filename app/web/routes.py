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
def home(request: Request) -> HTMLResponse:
    """« Trois décisions du jour » — phase 1: positions / protections / risques (docs/09 §9.5)."""
    from app.services.risk_view import build_risk_view

    cfg = request.app.state.config
    with db_session() as s:
        view = build_risk_view(s, cfg)
    decisions = []
    for p in view["unprotected"]:
        decisions.append(
            {
                "level": "P1",
                "text": f"Saisir un stop chez le courtier pour {p.isin} ({p.qty - p.qty_protected} titres non protégés)",  # noqa: E501
                "href": "/positions",
            }
        )
    r = view["risk"]
    if r.open_risk_pct > view["max_open_risk_pct"]:
        decisions.append(
            {
                "level": "P2",
                "text": "Réduire le risque ouvert (plafond dépassé) : alléger une position ou annuler un ordre en attente",  # noqa: E501
                "href": "/positions",
            }
        )
    for a in r.alerts:
        decisions.append({"level": "P2", "text": a, "href": None})
    return templates.TemplateResponse(
        request,
        "home.html",
        {"request": request, "view": view, "decisions": decisions[:3], "mode": current_mode_payload(request)},
    )


@router.get("/sante", response_class=HTMLResponse)
def sante(request: Request) -> HTMLResponse:
    st = request.app.state
    cfg = st.config
    now = datetime.now(UTC)
    from sqlalchemy import func, select

    from app.db.models import LlmCall

    with db_session() as s:
        runs = last_runs(s)
        fresh = freshness(s)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        llm_cost = float(
            s.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.ts >= month_start)) or 0.0
        )
        llm_calls = int(s.scalar(select(func.count(LlmCall.id)).where(LlmCall.ts >= month_start)) or 0)
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
        "llm_cost": llm_cost,
        "llm_calls": llm_calls,
        "llm_budget": cfg.params.llm.monthly_budget_usd if cfg.params.llm else None,
        "llm_enabled": bool(getattr(st, "llm", None) and st.llm.enabled),
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
