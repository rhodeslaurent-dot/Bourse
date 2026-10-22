"""``fixing_card`` (17:15 Paris, market days): « repli sur support » entries at the closing fixing
(docs/06 §6.7, R6), checked with the quote at analysis time (R19)."""

from __future__ import annotations

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.fixing import candidates, check, send_cards
from app.services.quotes import plausible_quotes


def run(ctx: JobContext) -> int:
    with db_session() as s:
        isins = sorted({p.isin for p, _ in candidates(s)})
        lines = check(s, ctx.config, plausible_quotes(s, isins, ctx.config) if isins else {})
        sent = send_cards(s, ctx.alerts, ctx.mode, lines) if ctx.alerts is not None else 0
    ctx.note = f"{len(lines)} repli(s) vérifié(s), {sent} fiche(s) de fixing"
    return sent
