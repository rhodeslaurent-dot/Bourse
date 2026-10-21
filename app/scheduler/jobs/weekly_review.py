"""``weekly_review`` (Saturday 08:00 Paris): refresh outcomes, then the AI review of the learning
dossier — findings, questions to the user, improvement suggestions (docs/18 §18.6, docs/12 §12.4).
Without an API key the review stores the computed aggregates only."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.learning import run_review
from app.services.outcomes import learning_params, update_outcomes


def run(ctx: JobContext) -> int:
    if learning_params(ctx.config) is None:
        ctx.note = "groupe learning absent de params.yaml : revue désactivée"
        return 0
    with db_session() as s:
        update_outcomes(s, ctx.config, ctx.calendars)
        rev = run_review(s, ctx.config, ctx.llm, trigger="weekly")
        ctx.note = {"ok": "revue IA", "rules": "statistiques seules (IA non configurée)"}.get(rev.status, rev.status)
        return len(rev.findings or [])
