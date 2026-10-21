"""``/journee`` (docs/17 §17.6): guided daily steps; a click runs a job, opens a page or ticks."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.routes import current_mode_payload
from app.db.session import db_session
from app.domain.timeutil import PARIS, REUNION
from app.services.notes import get_note
from app.services.workflow import SLOTS, day_view, find_step, mark
from app.web.routes import templates

router = APIRouter()


def _today() -> datetime:
    return datetime.now(PARIS)


@router.get("/journee", response_class=HTMLResponse)
def journee(request: Request) -> HTMLResponse:
    st = request.app.state
    now = _today()
    day = now.date()
    mode = current_mode_payload(request)["mode"]
    market_open = bool(st.calendars.open_mics(day, list(st.calendars.p0_mics)))
    slots_local = (st.config.params.user or {}).get("work_slots_local", {})
    with db_session() as s:
        steps = day_view(s, st.config.params, day, market_open, str(mode))
        todo = get_note(s, "global", "todo")
        todo_view = {"text": todo.text, "updated_at": todo.updated_at} if todo else None
    groups: list[dict] = []
    for slot, (title, key) in SLOTS.items():
        items = [x for x in steps if x.slot == slot]
        if not items:
            continue
        hour = slots_local.get(key) if key else None
        if isinstance(hour, list):
            hour = hour[0]
        when = None
        if hour:
            hh, mm = (int(x) for x in str(hour).split(":"))
            paris = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            when = f"{paris.astimezone(REUNION).strftime('%H:%M')} Réunion ({paris.strftime('%H:%M')} Paris)"
        applicable = [x for x in items if x.applicable]
        groups.append(
            {
                "slot": slot,
                "title": title,
                "when": when,
                "items": items,
                "done": sum(1 for x in applicable if x.done_at),
                "total": len(applicable),
            }
        )
    total = sum(g["total"] for g in groups)
    done = sum(g["done"] for g in groups)
    return templates.TemplateResponse(
        request,
        "journee.html",
        {
            "request": request,
            "groups": groups,
            "day": day,
            "mode": mode,
            "market_open": market_open,
            "done": done,
            "total": total,
            "todo": todo_view,
        },
    )


@router.post("/journee/step")
def do_step(request: Request, key: str = Form(...), action: str = Form("do")) -> RedirectResponse:
    st = request.app.state
    step = find_step(st.config.params, key)
    if step is None:
        raise HTTPException(404, "étape inconnue")
    day = _today().date()
    if action == "undo":
        with db_session() as s:
            mark(s, day, key, False)
        return RedirectResponse("/journee", status_code=303)
    if step["kind"] == "job":
        from app.scheduler.runner import execute_job

        if step["target"] not in st.config.params.jobs.specs():
            raise HTTPException(400, f"job {step['target']} absent de params.yaml")
        run = execute_job(
            step["target"],
            st.config,
            st.calendars,
            run_date=day,
            alerts=getattr(st, "alerts", None),
            mode=str(current_mode_payload(request)["mode"]),
            llm=getattr(st, "llm", None),
        )
        result = f"{run.status} · {run.rows if run.rows is not None else '—'} ligne(s)" + (
            f" · {run.note}" if run.note else ""
        )
        if run.error:
            result += f" · {run.error.splitlines()[0][:160]}"
        with db_session() as s:
            mark(s, day, key, run.status in ("ok", "skipped"), result)
        return RedirectResponse("/journee", status_code=303)
    with db_session() as s:
        mark(s, day, key, True, "ouvert" if step["kind"] == "page" else "fait")
    if step["kind"] == "page" and str(step.get("target", "")).startswith("/"):
        return RedirectResponse(step["target"], status_code=303)
    return RedirectResponse("/journee", status_code=303)
