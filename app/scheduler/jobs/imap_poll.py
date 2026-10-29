"""``imap_poll`` (every 10 min, 07:30–19:00): read the dedicated mailbox, ingest newsletters with
their receipt time and the quote at receipt (delayed source in phase 1, status kept)."""

from __future__ import annotations

import logging
import os

from sqlalchemy import select

from app.data.providers.imap_gmail import GmailImap
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.newsletters import ingest_mail
from app.services.press_review import press_rules
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
        senders, mailbox=str(imap.get("mailbox", "INBOX")), include_read=bool(imap.get("include_read", True))
    )
    follow = follow_rules(imap)
    cookie = os.environ.get("ZONEBOURSE_COOKIE") or None
    n = 0
    new_hashes: list[str] = []
    with db_session() as s:
        from app.db.models import NewsletterItem

        for mail in mails:
            if s.scalar(select(NewsletterItem.id).where(NewsletterItem.dedup_hash == mail.dedup_hash)) is not None:
                continue  # already ingested (read letters are searched too): no page fetched again
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
                press = press_rules(ctx.config).get(mail.source)
                if press is not None:
                    read_press_review(s, ctx, mail, press)
        if new_hashes:
            review_at_reception(s, ctx, new_hashes)
            engine_at_reception(s, ctx, new_hashes)
    return n


def engine_at_reception(s, ctx: JobContext, dedup_hashes: list[str]) -> None:  # type: ignore[no-untyped-def]
    """docs/22 §22.4 D, docs/20 §20.10: every value the letters just received recommend (« achat » or « à
    surveiller », ISIN resolved) is analysed by the engine right away with the quote at analysis time (R19) — one
    proposal of the day with its verdict, « non analysable » with the reason otherwise; the funnel of each letter is
    stored and summed up on the job's line."""
    from sqlalchemy import select

    from app.db.models import NewsletterItem
    from app.services.letter_analysis import analyse_items, funnel_text, recommended_isins

    items = list(s.scalars(select(NewsletterItem).where(NewsletterItem.dedup_hash.in_(dedup_hashes))))
    if not items:
        return
    isins = recommended_isins(s, items)
    try:
        quotes = plausible_quotes(s, isins, ctx.config) if isins else {}
    except Exception:  # noqa: BLE001 — no quote is better than a guessed one: the stored close is used, said so
        quotes = {}
    rep, funnels = analyse_items(s, ctx.config, items, quotes)
    parts = [f"moteur à réception : {rep.note()}" if rep is not None else "", funnel_text(funnels) or ""]
    ctx.note = " · ".join(x for x in (ctx.note, *parts) if x)


def read_press_review(s, ctx: JobContext, mail, rule) -> None:  # type: ignore[no-untyped-def]
    """docs/21 §21.7 — articles of the letter's press review, read before the reception review."""
    from sqlalchemy import select

    from app.db.models import NewsletterItem
    from app.services.press_review import ingest_press_review

    item = s.scalar(select(NewsletterItem).where(NewsletterItem.dedup_hash == mail.dedup_hash))
    if item is None or not mail.html:
        return
    try:
        from app.services.news import configured_scope

        res = ingest_press_review(s, mail.html, item, rule, ctx.llm, scope=configured_scope(ctx.config))
        ok = sum(1 for r in res if r["statut"] == "ok")
        log.info("revue de presse %s : %d lien(s), %d article(s) lu(s)", mail.source, len(res), ok)
    except Exception as exc:  # noqa: BLE001 — the letter and its review never depend on the press review
        log.warning("revue de presse en échec (%s)", exc.__class__.__name__)


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
