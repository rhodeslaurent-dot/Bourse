"""``/risque`` (docs/28): the daily risk level recommended by the engine with its plain explanation, the
override of the day, the reference risk (capital × % per trade) and its history."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import RiskDialDaily, RiskSetting
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.services.risk_level import bounds, dial_params, reference, set_override, set_reference, today
from app.web.routes import templates

router = APIRouter()


def _paris_today() -> date:
    return datetime.now(UTC).astimezone(PARIS).date()


@router.get("/risque", response_class=HTMLResponse)
def risk_page(request: Request, msg: str | None = None) -> HTMLResponse:
    cfg = request.app.state.config
    d = _paris_today()
    with db_session() as s:
        ref = reference(s, cfg)
        row = today(s, d)
        hist = list(
            s.scalars(
                select(RiskDialDaily)
                .where(RiskDialDaily.day >= d - timedelta(days=30))
                .order_by(RiskDialDaily.day.desc())
            )
        )
        settings_hist = list(s.scalars(select(RiskSetting).order_by(RiskSetting.effective_at.desc()).limit(10)))
        level = row.applied if row is not None else 1.0
        caps = getattr(cfg.params.risk, "max_open_risk_pct_by_gate", None) or {"ouvert": 0.05}
        cap = float(caps.get("ouvert", 0.05))
        per_line = ref.risk_pct * level
        ctx = {
            "request": request,
            "ref": ref,
            "row": row,
            "level": level,
            "lines_possible": int(cap / per_line + 1e-9) if per_line > 0 else None,
            "cap_pct": cap,
            "hist": hist,
            "settings_hist": settings_hist,
            "dp": dial_params(cfg),
            "bounds": bounds(cfg),
            "msg": msg,
            "day": d,
        }
    return templates.TemplateResponse(request, "risque.html", ctx)


@router.post("/risque/niveau")
def override_level(
    request: Request, level: str = Form(""), reason: str = Form(""), reset: str = Form(""), rescan: str = Form("")
) -> RedirectResponse:
    cfg = request.app.state.config
    d = _paris_today()
    try:
        value = None if reset else float(level.replace(",", "."))
    except ValueError as exc:
        raise HTTPException(400, "niveau illisible (ex. 0,9 ou 1,1)") from exc
    with db_session() as s:
        try:
            set_override(s, cfg, d, value, reason.strip() or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        note = "niveau conseillé rétabli" if value is None else f"niveau × {value:g} appliqué aujourd'hui"
        if rescan:
            from app.services.engine import rescan_today

            n = rescan_today(s, cfg, d)
            note += f" · {n} proposition(s) recalculée(s)"
    return RedirectResponse(f"/risque?msg={note}", status_code=303)


@router.post("/risque/reference")
def change_reference(
    request: Request, capital: str = Form(...), risk_pct: str = Form(...), reason: str = Form("")
) -> RedirectResponse:
    cfg = request.app.state.config
    try:
        cap = float(capital.replace(" ", "").replace(",", "."))
        pct = float(risk_pct.replace(",", ".")) / 100
    except ValueError as exc:
        raise HTTPException(400, "valeurs illisibles") from exc
    with db_session() as s:
        try:
            set_reference(s, cfg, cap, pct, reason.strip() or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/risque?msg=unité de risque : {cap * pct:.0f} € par ligne au niveau × 1", status_code=303)
