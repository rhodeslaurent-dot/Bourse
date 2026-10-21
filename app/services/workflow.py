"""Guided day (docs/17 §17.6): steps from ``params.yaml › workflow.steps`` with a per-day state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import WorkflowStep

SLOTS = {
    "C1": ("Brief pré-ouverture", "brief"),
    "C2": ("Ouverture", "open"),
    "C3": ("Revue du soir", "evening"),
    "hebdo": ("Revue hebdomadaire", None),
    "systeme": ("Système", None),
}
KINDS = ("job", "page", "check")


@dataclass
class StepView:
    key: str
    slot: str
    label: str
    kind: str
    target: str
    applicable: bool
    why_not: str | None
    done_at: datetime | None
    result: str | None


def _days(spec: str) -> set[int]:
    out: set[int] = set()
    for part in str(spec or "1-7").split(","):
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            out.update(range(a, b + 1))
        elif part.strip():
            out.add(int(part))
    return out


def steps_spec(params: Any) -> list[dict[str, Any]]:
    wf = getattr(params, "workflow", None) or {}
    steps = [dict(x) for x in (wf.get("steps") or []) if isinstance(x, dict) and x.get("key")]
    for st in steps:
        if st.get("kind") not in KINDS:
            st["kind"] = "check"
    return steps


def day_view(s: Session, params: Any, day: date, market_open: bool, mode: str) -> list[StepView]:
    state = {r.key: r for r in s.scalars(select(WorkflowStep).where(WorkflowStep.day == day))}
    out: list[StepView] = []
    for st in steps_spec(params):
        applicable, why = True, None
        if day.isoweekday() not in _days(st.get("days", "1-7")):
            applicable, why = False, "pas aujourd'hui"
        elif st.get("slot") in ("C1", "C2", "C3") and not market_open:
            applicable, why = False, "marché fermé (calendrier)"
        elif st.get("slot") == "C2" and mode != "disponible":
            applicable, why = False, f"mode {mode}"
        row = state.get(st["key"])
        out.append(
            StepView(
                key=st["key"],
                slot=st.get("slot", "systeme"),
                label=st.get("label", st["key"]),
                kind=st["kind"],
                target=str(st.get("target") or ""),
                applicable=applicable,
                why_not=why,
                done_at=row.done_at if row else None,
                result=row.result if row else None,
            )
        )
    return out


def mark(
    s: Session, day: date, key: str, done: bool, result: str | None = None, now: datetime | None = None
) -> WorkflowStep:
    row = s.scalar(select(WorkflowStep).where(WorkflowStep.day == day, WorkflowStep.key == key))
    if row is None:
        row = WorkflowStep(day=day, key=key)
        s.add(row)
    row.done_at = (now or datetime.now(UTC)) if done else None
    row.result = result
    s.flush()
    return row


def find_step(params: Any, key: str) -> dict[str, Any] | None:
    return next((st for st in steps_spec(params) if st["key"] == key), None)
