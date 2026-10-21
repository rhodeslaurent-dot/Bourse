"""Learning (docs/18): mark duplicated open suggestions / questions created by repeated reviews.

The oldest of each group is kept; the others get status ``duplicate`` (suggestions) or
``dismissed`` (questions). Nothing is deleted.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations  # noqa: I001

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def norm_text(text: str | None) -> str:
    """Frozen copy of app.services.learning.norm_text (migrations must not depend on app code)."""
    import unicodedata

    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


def upgrade() -> None:
    conn = op.get_bind()
    seen: set[tuple[str, str]] = set()
    rows = conn.execute(
        sa.text("SELECT id, kind, title FROM learning_suggestions WHERE status IN ('open', 'later') ORDER BY id")
    ).fetchall()
    for rid, kind, title in rows:
        key = (kind, norm_text(title))
        if key in seen:
            conn.execute(sa.text("UPDATE learning_suggestions SET status = 'duplicate' WHERE id = :id"), {"id": rid})
        seen.add(key)
    asked: set[str] = set()
    rows = conn.execute(
        sa.text("SELECT id, question, status FROM learning_questions WHERE status IN ('open', 'answered') ORDER BY id")
    ).fetchall()
    for rid, question, status in rows:
        key = norm_text(question)
        if key in asked and status == "open":
            conn.execute(sa.text("UPDATE learning_questions SET status = 'dismissed' WHERE id = :id"), {"id": rid})
        asked.add(key)


def downgrade() -> None:
    op.get_bind().execute(sa.text("UPDATE learning_suggestions SET status = 'open' WHERE status = 'duplicate'"))
