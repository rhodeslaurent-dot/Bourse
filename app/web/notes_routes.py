"""Free-text notes (docs/17 §17.7): one HTML form per note, saved in place, redirect back."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse

from app.db.session import db_session
from app.services.notes import set_note

router = APIRouter()


@router.post("/notes")
def save_note(
    scope: str = Form(...), ref: str = Form(...), text: str = Form(""), next: str = Form("/")
) -> RedirectResponse:
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    with db_session() as s:
        try:
            set_note(s, scope, ref, text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(next, status_code=303)
