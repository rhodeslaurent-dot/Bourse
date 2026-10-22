"""``imap_poll`` (every 10 min, 07:30–19:00): read the dedicated mailbox, ingest newsletters with
their receipt time and the quote at receipt (delayed source in phase 1, status kept)."""

from __future__ import annotations

import logging
import os

from app.data.providers.imap_gmail import GmailImap
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.newsletters import ingest_mail
from app.services.quotes import plausible_quotes

log = logging.getLogger("bourse.jobs.imap_poll")


# Used when params.yaml has no data.imap.follow_links (same values as params.example.yaml).
DEFAULT_FOLLOW_LINKS = [
    {
        "source": "zonebourse",
        "link_texts": ["Lire la suite"],
        "allowed_domains": ["zonebourse.com"],
        "tracking_domains": ["mjt.lu"],
        "min_interval_s": 3,
        "timeout_s": 15,
    }
]


def subject_ok(imap: dict, source: str, subject: str) -> bool:
    """``data.imap.subject_includes``: keep only letters whose subject contains one of the words
    configured for their source (none configured → all letters of the source are kept)."""
    words = [w.lower() for w in ((imap.get("subject_includes") or {}).get(source) or []) if w]
    return not words or any(w in (subject or "").lower() for w in words)


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
    new_hashes: list[str] = []
    with db_session() as s:
        for mail in mails:
            if not subject_ok(imap, mail.source, mail.subject):
                log.info("lettre %s ignorée (objet hors subject_includes) : %s", mail.source, mail.subject[:80])
                continue
            rule = follow.get(mail.source)
            if rule is not None:
                from app.data.providers.linked_page import enrich_mail

                mail, page = enrich_mail(mail, rule, cookie=cookie)
                log.info("page liée %s : %s", mail.source, page.status)
            from app.data.providers.imap_gmail import extract_values
            from app.services.news import alias_index

            isins = [v.isin for v in extract_values(mail.body_text, alias_index(s)) if v.isin]
            rep = ingest_mail(
                s,
                mail,
                ctx.config,
                ctx.llm,
                plausible_quotes(s, isins, ctx.config) if isins else {},
                calendars=ctx.calendars,
            )
            n += rep.signals
            if rep.new:
                new_hashes.append(mail.dedup_hash)
        if new_hashes:
            review_at_reception(s, ctx, new_hashes)
            engine_at_reception(s, ctx, new_hashes)
    return n


def engine_at_reception(s, ctx: JobContext, dedup_hashes: list[str]) -> None:  # type: ignore[no-untyped-def]
    """docs/22 §22.4 D: the testable setups of the letters just received are recalculated by the
    engine right away (P_max, full cost, caps, ranking) with the quote at analysis time (R19)."""
    from sqlalchemy import select

    from app.db.models import NewsletterItem, NewsletterValue
    from app.services.engine import enabled, scan

    if not enabled(ctx.config):
        return
    items = list(s.scalars(select(NewsletterItem).where(NewsletterItem.dedup_hash.in_(dedup_hashes))))
    isins = sorted(
        {
            v.isin
            for v in s.scalars(select(NewsletterValue).where(NewsletterValue.item_id.in_([i.id for i in items])))
            if v.isin and (v.levels or {}).get("stop") is not None and (v.levels or {}).get("objectif") is not None
        }
    )
    if not isins:
        return
    try:
        from datetime import datetime

        from app.domain.timeutil import PARIS

        rep = scan(
            s, ctx.config, datetime.now(PARIS).date(), isins=isins, quotes=plausible_quotes(s, isins, ctx.config)
        )
        ctx.note = " · ".join(x for x in (ctx.note, f"moteur à réception : {rep.note()}") if x)
    except Exception as exc:  # noqa: BLE001 — the morning scan recalculates what is left
        log.warning("moteur à réception en échec (%s)", exc.__class__.__name__)


def review_at_reception(s, ctx: JobContext, dedup_hashes: list[str]) -> None:  # type: ignore[no-untyped-def]
    """docs/20 §20.1: the letters just received are reviewed by Claude right away (signals still
    actionable); a failure leaves them for the evening catch-up, never breaks the poll."""
    from sqlalchemy import select

    from app.db.models import NewsletterItem
    from app.services.letters_review import params_of, run_review

    if params_of(ctx.config) is None:
        return
    from app.services.letters_review import cited_isins

    items = list(s.scalars(select(NewsletterItem).where(NewsletterItem.dedup_hash.in_(dedup_hashes))))
    try:
        isins = cited_isins(s, items)[:15]
        try:
            quotes = plausible_quotes(s, isins, ctx.config) if isins else {}  # R19: quote at analysis time
        except Exception:  # noqa: BLE001 — no quote is better than a guessed one
            quotes = {}
        rev = run_review(
            s, ctx.config, ctx.llm, trigger="reception", alerts=ctx.alerts, mode=ctx.mode, items=items, quotes=quotes
        )
        ctx.note = f"revue à réception : {len(items)} lettre(s), {rev.status}"
    except Exception as exc:  # noqa: BLE001 — the evening catch-up reviews what is left
        log.warning("revue à réception en échec (%s) : rattrapage au passage du soir", exc.__class__.__name__)
