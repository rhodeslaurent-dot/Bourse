"""Free-text notes (docs/17 §17.7): one current note per (scope, ref), edited in place."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Note

SCOPES = ("isin", "position", "watchlist", "proposal", "global")


def get_note(s: Session, scope: str, ref: str) -> Note | None:
    return s.scalar(select(Note).where(Note.scope == scope, Note.ref == str(ref)))


def notes_for(s: Session, scope: str, refs: list[str]) -> dict[str, Note]:
    if not refs:
        return {}
    return {n.ref: n for n in s.scalars(select(Note).where(Note.scope == scope, Note.ref.in_([str(r) for r in refs])))}


def set_note(s: Session, scope: str, ref: str, text: str, now: datetime | None = None) -> Note:
    """Create or update; never deletes (an empty text empties the note and keeps its row)."""
    if scope not in SCOPES:
        raise ValueError(f"scope inconnu : {scope}")
    now = now or datetime.now(UTC)
    row = get_note(s, scope, ref)
    if row is None:
        row = Note(scope=scope, ref=str(ref), text="", updated_at=now)
        s.add(row)
    row.text = text.strip()[:4000]
    row.updated_at = now
    s.flush()
    return row
