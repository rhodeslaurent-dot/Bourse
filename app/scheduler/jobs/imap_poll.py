"""``imap_poll`` (every 10 min, 07:30–19:00): read the dedicated mailbox, ingest newsletters with
their receipt time and the quote at receipt (delayed source in phase 1, status kept)."""

from __future__ import annotations

import logging
import os

from app.data.providers.imap_gmail import GmailImap
from app.db.session import db_session
from app.scheduler.jobs.position_monitor import quotes_for
from app.scheduler.runner import JobContext
from app.services.newsletters import ingest_mail

log = logging.getLogger("bourse.jobs.imap_poll")


def run(ctx: JobContext) -> int:
    imap = ctx.config.params.data.imap or {}
    if not (os.environ.get("IMAP_USER") and os.environ.get("IMAP_PASSWORD")):
        log.info("IMAP non configuré : rappel manuel (docs/14 dégradation contrôlée)")
        return 0
    senders = {k: list(v) for k, v in (imap.get("senders") or {}).items()}
    mails = GmailImap(str(imap.get("host", "imap.gmail.com"))).fetch_unseen(senders)
    n = 0
    with db_session() as s:
        for mail in mails:
            from app.data.providers.imap_gmail import extract_values
            from app.services.news import alias_index

            isins = [v.isin for v in extract_values(mail.body_text, alias_index(s)) if v.isin]
            rep = ingest_mail(
                s, mail, ctx.config, ctx.llm, quotes_for(isins, ctx.config) if isins else {}, calendars=ctx.calendars
            )
            n += rep.signals
    return n
