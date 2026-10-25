"""``kpi_snapshot`` (18:10 Paris, market days): equity of the day per account → ``kpi_daily``
(docs/17 §17.5). Idempotent; an incomplete day is stored as such, never estimated. Once a month (first
snapshot of the month), the BoursoBank formula review: a P3 when another formula would cost less."""

from __future__ import annotations

from app.db.session import db_session
from app.notify.base import Event
from app.scheduler.runner import JobContext
from app.services.performance import snapshot_equity


def run(ctx: JobContext) -> int:
    with db_session() as s:
        n = len(snapshot_equity(s, ctx.config, ctx.run_date))
        if ctx.alerts is not None and ctx.run_date.day <= 7:  # the month's first week: one review per month
            from sqlalchemy import select

            from app.db.models import Alert
            from app.services.broker_plan import analysis

            month0 = ctx.run_date.replace(day=1)
            if s.scalar(
                select(Alert.id).where(Alert.kind == "broker_plan", Alert.day >= month0, Alert.sent_at.is_not(None))
            ):
                return n

            a = analysis(s, ctx.config)
            c = (a or {}).get("comparison")
            if c is not None and c.best and c.best != c.current:
                ctx.alerts.emit(
                    s,
                    Event(
                        "P3",
                        "broker_plan",
                        "Formule de courtage BoursoBank",
                        [
                            c.recommendation,
                            f"Base : {a['basis']}",
                            "Détail : /performance#formule",
                            "Grille à vérifier sur la brochure BoursoBank avant de changer",
                        ],
                    ),  # fmt: skip
                    ctx.mode,
                    intraday=False,
                )
    return n
