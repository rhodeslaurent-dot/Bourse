"""Server-rendered pages (Jinja2 + HTMX + Tailwind CDN), mobile-first."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.api.routes import current_mode_payload
from app.api.security import cf_settings
from app.db.repo import freshness, last_runs, set_override
from app.db.session import db_session
from app.domain.availability import unconfirmed_slots
from app.domain.timeutil import PARIS, REUNION, format_dual, from_store
from app.scheduler.registry import IMPLEMENTED_JOBS
from app.scheduler.runner import DEFAULT_WATCHDOG_JOBS
from app.services.instruments import name_of

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["dual"] = lambda dt: format_dual(from_store(dt))
templates.env.filters["dual_dated"] = lambda dt: (
    f"{from_store(dt).astimezone(REUNION):%d/%m} {format_dual(from_store(dt))}"
)


def _eur(value: object, digits: int = 0) -> str:
    """``12345.6`` → ``12 346 €`` (thin-space thousands); ``None`` → ``—`` (never an estimate)."""
    if value is None:
        return "—"
    return f"{float(value):,.{digits}f}".replace(",", "\u202f") + "\u00a0€"


def _pct(value: object, digits: int = 1) -> str:
    return "—" if value is None else f"{float(value) * 100:.{digits}f}\u00a0%"


templates.env.filters["eur"] = _eur
templates.env.filters["pct"] = _pct
from app.web.labels import FEATURE_LABELS, help_of, label  # noqa: E402

templates.env.globals.update(label=label, help_of=help_of, feature_labels=FEATURE_LABELS)


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """« Trois décisions du jour » — phase 1: positions / protections / risques (docs/09 §9.5)."""
    from app.services.risk_view import build_risk_view

    cfg = request.app.state.config
    from app.services.circuit import circuit_state

    with db_session() as s:
        view = build_risk_view(s, cfg)
        try:
            circuit = circuit_state(s, cfg)
        except Exception:  # noqa: BLE001 — the page shows « indisponible », never a guess
            circuit = None
    decisions = []
    if circuit is not None and circuit.tripped:
        decisions.append({"level": "P2", "text": f"Pas de nouvelle entrée — {circuit.reason}", "href": "/performance"})
    for p in view["unprotected"]:
        decisions.append(
            {
                "level": "P1",
                "text": f"Saisir un stop chez le courtier pour {name_of(p.isin) or p.isin} ({p.qty - p.qty_protected} titres non protégés)",  # noqa: E501
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
    from app.db.models import LearningQuestion

    with db_session() as s:
        n_questions = len(list(s.scalars(select(LearningQuestion.id).where(LearningQuestion.status == "open"))))
    if n_questions:
        decisions.append(
            {
                "level": "P3",
                "text": f"L'IA a {n_questions} question(s) pour vous sur le retour d'expérience",
                "href": "/apprentissage#questions",
            }
        )
    from app.db.models import MarketRegimeRow, Proposal, WatchlistRow
    from app.services.workflow import day_view

    paris_today = datetime.now(PARIS).date()
    with db_session() as s:
        regime = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
        wl_count = len(list(s.scalars(select(WatchlistRow.id).where(WatchlistRow.active.is_(True)))))
        props_today = [
            p
            for p in s.scalars(
                select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id.desc()).limit(200)
            )
            if (p.created_at.replace(tzinfo=UTC) if p.created_at.tzinfo is None else p.created_at).date()
            == datetime.now(UTC).date()
        ]
        steps = [
            x
            for x in day_view(
                s,
                cfg.params,
                paris_today,
                bool(request.app.state.calendars.open_mics(paris_today, list(request.app.state.calendars.p0_mics))),
                "reunion",
            )
            if x.applicable
        ]
        from app.services.macro import LABELS as MACRO_LABELS
        from app.services.macro import market_context, upcoming_events

        macro = market_context(s, cfg, paris_today)
        macro_events = upcoming_events(s, datetime.now(UTC), days=7)
        summary = {
            "regime": regime.regime if regime else None,
            "regime_day": regime.day if regime else None,
            "watchlist": wl_count,
            "props": len(props_today),
            "props_open": sum(1 for p in props_today if p.status == "open"),
            "steps_done": sum(1 for x in steps if x.done_at),
            "steps_total": len(steps),
        }
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "view": view,
            "circuit": circuit,
            "config_cb": cfg.params.risk.circuit_breaker,
            "decisions": decisions[:3],
            "mode": current_mode_payload(request),
            "summary": summary,
            "macro": macro,
            "macro_events": macro_events,
            "macro_labels": MACRO_LABELS,
            "names": {i: name_of(i) for i in {p.isin for p in view["unprotected"]} | set(view["price_status"] or {})},
        },
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
        from app.services.llm_costs import cost_board

        llm_board = cost_board(s, now)
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
    specs = cfg.params.jobs.specs()
    for name in IMPLEMENTED_JOBS:
        if name not in specs:  # declared in params.example.yaml after this params.yaml was created
            jobs_view.append(
                {
                    "name": name,
                    "implemented": True,
                    "enabled": False,
                    "market_days_only": False,
                    "missing": True,
                    "next": None,
                    "last": runs.get(name),
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
        "local_writes": os.environ.get("ALLOW_UNAUTHENTICATED_WRITES") == "1",
        "watchdog": bool(os.environ.get("HEALTHCHECKS_PING_KEY")),
        "watchdog_jobs": list(getattr(cfg.params.notifications, "watchdog_jobs", None) or DEFAULT_WATCHDOG_JOBS),
        "calendars": st.calendars,
        "telegram": st.telegram_enabled,
        "email": st.email_enabled,
        "db_url_kind": st.db_kind,
        "llm_cost": llm_cost,
        "llm_calls": llm_calls,
        "llm_board": llm_board,
        "llm_daily_budget": getattr(cfg.params.llm, "daily_budget_usd", None) if cfg.params.llm else None,
        "llm_budget": cfg.params.llm.monthly_budget_usd if cfg.params.llm else None,
        "llm_enabled": bool(getattr(st, "llm", None) and st.llm.enabled),
        "saxo": saxo_status(),
        "eodhd": bool(__import__("os").environ.get("EODHD_API_TOKEN")),
    }
    return templates.TemplateResponse(request, "sante.html", ctx)


def saxo_status() -> dict:
    """Token state without any network call (ADR-007): ``configured``, ``present``, ``expired``, ``until``."""
    import os

    out: dict = {
        "configured": bool(os.environ.get("SAXO_TOKEN_KEY")),
        "present": False,
        "expired": False,
        "until": None,
    }
    if not out["configured"]:
        return out
    try:
        from app.data.providers.saxo import TokenStore

        info = TokenStore().info()
    except Exception:  # noqa: BLE001 — unreadable file (wrong key) counts as absent
        info = None
    if info:
        out["present"] = True
        out["expired"] = bool(info.get("expired"))
        exp = info.get("refresh_expires_at")
        out["until"] = datetime.fromtimestamp(float(exp), UTC).astimezone(REUNION) if exp else None
    return out


@router.post("/mode")
def set_mode_form(
    request: Request, mode: str = Form(...), duration_minutes: int | None = Form(default=None)
) -> RedirectResponse:
    from datetime import timedelta

    until = datetime.now(UTC) + timedelta(minutes=duration_minutes) if duration_minutes else None
    with db_session() as s:
        set_override(s, mode, until, "web")
    return RedirectResponse("/sante", status_code=303)
