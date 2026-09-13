"""JSON API (docs/09 §9.6) — phase 0 subset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db.repo import current_override, last_runs, set_override
from app.db.session import db_session
from app.domain.availability import MODES, resolve_mode
from app.domain.timeutil import PARIS, REUNION

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Minimal, unauthenticated (docs/14 §14.1)."""
    return {"status": "ok"}


@router.get("/api/health")
def api_health(request: Request) -> dict[str, object]:
    st = request.app.state
    now = datetime.now(UTC)
    return {
        "status": "ok",
        "version": st.config.params.version,
        "params_sha256": st.config.sha256[:12],
        "now_utc": now.isoformat(),
        "now_paris": now.astimezone(PARIS).strftime("%Y-%m-%d %H:%M"),
        "now_reunion": now.astimezone(REUNION).strftime("%Y-%m-%d %H:%M"),
        "scheduler_running": bool(getattr(st, "scheduler", None) and st.scheduler.running),
        "disabled_features": [f.key for f in st.config.disabled_features],
    }


@router.get("/api/jobs")
def api_jobs(request: Request) -> dict[str, object]:
    st = request.app.state
    sched = getattr(st, "scheduler", None)
    scheduled = []
    if sched is not None:
        for j in sched.get_jobs():
            scheduled.append({"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None})
    with db_session() as s:
        runs = {
            k: {"status": v.status, "started_at": v.started_at.isoformat(), "note": v.note}
            for k, v in last_runs(s).items()
        }
    return {"scheduled": scheduled, "last_runs": runs}


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(disponible|reunion|absent)$")
    duration_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 60)


def current_mode_payload(request: Request) -> dict[str, object]:
    st = request.app.state
    now = datetime.now(UTC)
    av = st.config.params.availability
    schedule = av.schedule if av and st.config.feature_enabled("availability.schedule") else []
    default_mode = av.default_mode if av else "reunion"
    with db_session() as s:
        ov = current_override(s, now)
    r = resolve_mode(now, schedule, default_mode, ov)
    return {
        "mode": r.mode,
        "source": r.source,
        "until": r.until.isoformat() if r.until else None,
        "slot": r.slot,
    }


@router.get("/api/mode")
def get_mode(request: Request) -> dict[str, object]:
    return current_mode_payload(request)


@router.post("/api/mode")
def post_mode(body: ModeIn, request: Request) -> dict[str, object]:
    if body.mode not in MODES:
        raise HTTPException(400, "mode invalide")
    until = datetime.now(UTC) + timedelta(minutes=body.duration_minutes) if body.duration_minutes else None
    with db_session() as s:
        set_override(s, body.mode, until, "api" if getattr(request.state, "auth", "") == "app_token" else "web")
    return current_mode_payload(request)
