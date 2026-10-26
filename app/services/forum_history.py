"""docs/26 (user decision of 26/10/2026, option B): the forum history of the Paris values back to
``weak_signals.history.forum_from``, read slowly over several nights (one list page every ``interval_s``), so that
« buzz précoce » can be measured on years of data (research registration n° 2). Topics are counted on their
**creation** day (a fixed date), each thread number once; a day is a true zero only when the pages read cover it.
A refusal of the site (HTTP 403 / 429) stops the reading for ``block_hours`` — never a retry loop."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.data.base import ProviderError
from app.data.providers import social
from app.db.models import AttentionDaily, ForumHistoryProgress
from app.domain.timeutil import PARIS
from app.services.social import followed_values, forum_url, social_params

log = logging.getLogger("bourse.forum_history")
SOURCE = "forum_boursorama"


def history_params(config: LoadedConfig) -> dict[str, Any]:
    h = dict(social_params(config).get("history") or {})
    h.setdefault("enabled", True)
    h.setdefault("forum_from", "2019-01-01")
    h.setdefault("values_max", 200)
    h.setdefault("pages_per_run", 400)
    h.setdefault("block_hours", 24)
    h.setdefault("row_class", "c-table__row")
    return h


def _bump(s: Session, isin: str, d: date, now: datetime) -> None:
    row = s.scalar(
        select(AttentionDaily).where(
            AttentionDaily.isin == isin, AttentionDaily.day == d, AttentionDaily.source == SOURCE
        )
    )
    if row is None:
        s.add(AttentionDaily(isin=isin, day=d, source=SOURCE, lang="-", value=1, received_at=now))
    else:
        row.value, row.received_at = row.value + 1, now
    s.flush()


def _zeros(s: Session, isin: str, first: date, cutoff: date, now: datetime) -> int:
    have = set(
        s.scalars(
            select(AttentionDaily.day).where(
                AttentionDaily.isin == isin, AttentionDaily.source == SOURCE, AttentionDaily.day >= first,
                AttentionDaily.day < cutoff,
            )
        )
    )  # fmt: skip
    n, d = 0, first
    while d < cutoff:
        if d not in have:
            s.add(AttentionDaily(isin=isin, day=d, source=SOURCE, lang="-", value=0, received_at=now))
            n += 1
        d += timedelta(days=1)
    return n


def run_pass(
    s: Session,
    config: LoadedConfig,
    client: httpx.Client,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """One night slice: at most ``pages_per_run`` list pages, value after value, resuming where the last pass
    stopped. Returns notes (French) for the job run."""
    h = history_params(config)
    if not h["enabled"]:
        return ["historique du forum désactivé (weak_signals.social.history.enabled)"]
    now = now or datetime.now(UTC)
    blocked = s.scalar(select(func.max(ForumHistoryProgress.blocked_until)))
    if blocked is not None and (blocked if blocked.tzinfo else blocked.replace(tzinfo=UTC)) > now:
        return [f"forum en pause jusqu'au {blocked:%d/%m %H:%M} UTC (refus du site)"]
    bp = social_params(config).get("boursorama") or {}
    tpl = bp.get("url_template_by_mic") or {}
    suffix = bp.get("page_suffix_template") or "page-{n}"
    created_cls, active_cls = str(bp.get("date_class") or ""), str(bp.get("activity_class") or "")
    if not created_cls or not active_cls:
        return ["classes de date du forum non réglées (date_class, activity_class) : historique impossible"]
    start = date.fromisoformat(str(h["forum_from"]))
    today = now.astimezone(PARIS).date()
    paris_now = now.astimezone(PARIS)
    budget = int(h["pages_per_run"])
    pages = done_n = counted = 0
    values = followed_values(s, int(h["values_max"]), today, lambda i: forum_url(i, tpl) is not None)
    for inst in values:
        if budget <= 0:
            break
        prog = s.get(ForumHistoryProgress, inst.isin)
        if prog is None:
            first_stored = s.scalar(
                select(func.min(AttentionDaily.day)).where(
                    AttentionDaily.isin == inst.isin, AttentionDaily.source == SOURCE
                )
            )
            prog = ForumHistoryProgress(isin=inst.isin, next_page=1, cutoff=first_stored or today, seen=[], done=False)
            s.add(prog)
        if prog.done:
            done_n += 1
            continue
        url = forum_url(inst, tpl)
        assert url is not None
        seen = set(prog.seen or [])
        k, cutoff = prog.next_page, prog.cutoff or today
        while budget > 0 and not prog.done:
            page_url = url if k == 1 else url.rstrip("/") + "/" + suffix.format(n=k)
            try:
                html = social.boursorama_page(page_url, client)
            except ProviderError as exc:
                if "403" in str(exc) or "429" in str(exc):
                    prog.blocked_until = now + timedelta(hours=float(h["block_hours"]))
                    prog.next_page, prog.seen, prog.updated_at = k, sorted(seen), now
                    s.flush()
                    return [f"forum : refus du site ({exc}) — pause de {h['block_hours']} h, {pages} page(s) lues"]
                log.info("historique forum %s page %d : %s", inst.isin, k, exc)
                break
            except httpx.HTTPError as exc:
                log.info("historique forum %s page %d : %s", inst.isin, k, exc.__class__.__name__)
                break
            finally:
                sleep(float(bp.get("interval_s", 2.0)))
            budget -= 1
            pages += 1
            rows = social.topic_rows(html, paris_now, str(h["row_class"]), created_cls, active_cls)
            if not rows:
                prog.done = True
                break
            for tid, created, _ in rows:
                if tid in seen:
                    continue  # a thread moved during the reading: counted once
                seen.add(tid)
                if created is not None and start <= created < cutoff:
                    _bump(s, inst.isin, created, now)
                    counted += 1
            acts = [a for _, _, a in rows if a is not None]
            if acts:
                oldest = min(acts)
                prog.oldest_activity = min(prog.oldest_activity or oldest, oldest)
            if prog.oldest_activity is not None and prog.oldest_activity < start:
                prog.done = True
            k += 1
        prog.next_page, prog.seen, prog.updated_at = k, sorted(seen), now
        if prog.oldest_activity is not None:  # every thread created after the oldest activity read has been seen
            _zeros(s, inst.isin, max(start, prog.oldest_activity + timedelta(days=1)), cutoff, now)
        if prog.done:
            done_n += 1
        s.flush()
        s.commit()
    total = len(values)
    return [
        f"forum historique : {pages} page(s) lues, {counted} sujet(s) comptés, {done_n}/{total} valeur(s) terminées"
    ]


def progress_summary(s: Session) -> dict[str, int]:
    rows = list(s.scalars(select(ForumHistoryProgress)))
    return {"valeurs": len(rows), "terminees": sum(1 for r in rows if r.done)}
