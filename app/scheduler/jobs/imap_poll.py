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


# Used when params.yaml has no data.imap.follow_links (same values as params.example.yaml).
DEFAULT_FOLLOW_LINKS = [
    {
        "source": "zonebourse",
        "link_texts": ["Lire la suite"],
        "allowed_domains": ["zonebourse.com"],
        "tracking_domains": [],
        "min_interval_s": 3,
        "timeout_s": 15,
    }
]


def follow_rules(imap: dict) -> dict[str, dict]:
    """``data.imap.follow_links`` by source (docs/19 §19.6)."""
    rules = imap.get("follow_links")
    rules = DEFAULT_FOLLOW_LINKS if rules is None else rules
    return {r["source"]: r for r in rules if isinstance(r, dict) and r.get("source")}


def run(ctx: JobContext) -> int:
    imap = ctx.config.params.data.imap or {}
    if not (os.environ.get("IMAP_USER") and os.environ.get("IMAP_PASSWORD")):
        log.info("IMAP non configuré : rappel manuel (docs/14 dégradation contrôlée)")
        return 0
    senders = {k: list(v) for k, v in (imap.get("senders") or {}).items()}
    mails = GmailImap(str(imap.get("host", "imap.gmail.com"))).fetch_unseen(
        senders, mailbox=str(imap.get("mailbox", "INBOX"))
    )
    follow = follow_rules(imap)
    cookie = os.environ.get("ZONEBOURSE_COOKIE") or None
    n = 0
    with db_session() as s:
        for mail in mails:
            rule = follow.get(mail.source)
            if rule is not None:
                from app.data.providers.linked_page import enrich_mail

                mail, page = enrich_mail(mail, rule, cookie=cookie)
                log.info("page liée %s : %s", mail.source, page.status)
            from app.data.providers.imap_gmail import extract_values
            from app.services.news import alias_index

            isins = [v.isin for v in extract_values(mail.body_text, alias_index(s)) if v.isin]
            rep = ingest_mail(
                s, mail, ctx.config, ctx.llm, quotes_for(isins, ctx.config) if isins else {}, calendars=ctx.calendars
            )
            n += rep.signals
    return n
