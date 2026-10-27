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
from app.db.models import AttentionDaily, ForumHistoryProgress, Instrument
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
    h.setdefault("stale_pages_max", 3)  # consecutive pages without any new thread: the site's pagination is exhausted
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
    pages = done_n = counted = undated = 0
    capped: list[str] = []
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
        stale = 0
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
            new = 0
            for tid, created, _ in rows:
                if tid in seen:
                    continue  # a thread moved during the reading: counted once
                seen.add(tid)
                new += 1
                if created is None:
                    undated += 1  # creation date not read: never guessed (reported)
                elif start <= created < cutoff:
                    _bump(s, inst.isin, created, now)
                    counted += 1
            # 27/10/2026: past a page number, the site served the same pages again (4 400 pages, 1 518 threads):
            # pages without any new thread end the reading of the value — never a night spent on one forum
            stale = 0 if new else stale + 1
            if stale >= int(h["stale_pages_max"]):
                prog.done = True
                capped.append(f"{inst.isin} (page {k}, plus ancienne activité {prog.oldest_activity})")
                break
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
    notes = [
        f"forum historique : {pages} page(s) lues, {counted} sujet(s) comptés, {done_n}/{total} valeur(s) terminées"
    ]
    if undated:
        notes.append(
            f"{undated} sujet(s) sans date de création lisible (non comptés) : "
            "vérifier date_class (radar-history probe)"
        )
    if capped:
        notes.append("pagination du site épuisée avant forum_from : " + ", ".join(capped))
    return notes


def probe_pages(
    s: Session, config: LoadedConfig, client: httpx.Client, isin: str | None, pages: list[int]
) -> list[str]:
    """``radar-history probe`` (27/10/2026): a few list pages of one value — rows, threads new against the
    previous page, creation and activity days read (and how many could not be read), and the text shapes of the
    two date fields (digits as 9, letters as x: a format, never a word). Tells a capped pagination from an
    unreadable date format."""
    h = history_params(config)
    bp = social_params(config).get("boursorama") or {}
    tpl = bp.get("url_template_by_mic") or {}
    suffix = bp.get("page_suffix_template") or "page-{n}"
    created_cls, active_cls = str(bp.get("date_class") or ""), str(bp.get("activity_class") or "")
    inst = s.get(Instrument, isin) if isin else None
    url = forum_url(inst, tpl) if inst is not None else None
    if url is None:
        return [f"{isin} : valeur inconnue ou sans forum (url_template_by_mic)"]
    now = datetime.now(UTC).astimezone(PARIS)
    out: list[str] = []
    prev: set[str] = set()
    for k in pages:
        page_url = url if k == 1 else url.rstrip("/") + "/" + suffix.format(n=k)
        try:
            html = social.boursorama_page(page_url, client)
        except (ProviderError, httpx.HTTPError) as exc:
            out.append(f"page {k} : {exc}")
            continue
        finally:
            time.sleep(float(bp.get("interval_s", 2.0)))
        rows = social.topic_rows(html, now, str(h["row_class"]), created_cls, active_cls)
        ids = {r[0] for r in rows}
        cr = [r[1] for r in rows if r[1] is not None]
        ac = [r[2] for r in rows if r[2] is not None]
        shapes = {
            key: sh
            for key, _, sh in social.short_field_shapes(html, min_count=1)
            if created_cls in key or active_cls in key
        }
        out.append(
            f"page {k} : {len(rows)} sujet(s), {len(ids - prev)} nouveau(x) / page précédente lue ; création lue "
            f"{len(cr)}/{len(rows)} ({min(cr) if cr else '—'} → {max(cr) if cr else '—'}) ; activité "
            f"{min(ac) if ac else '—'} → {max(ac) if ac else '—'}"
        )
        for key, sh in sorted(shapes.items()):
            out.append(f"    {key} : {', '.join(sh[:6])}")
        prev = ids
    return out


def progress_summary(s: Session) -> dict[str, Any]:
    """Progress per value (pages read, oldest activity reached, pause) and the last runs of the job: enough to
    tell a long forum from a job that did not run or a site refusal (27/10/2026: « 0/1 terminée »)."""
    from app.db.models import JobRun

    rows = list(s.scalars(select(ForumHistoryProgress)))
    runs = list(s.scalars(select(JobRun).where(JobRun.job == "forum_history").order_by(JobRun.id.desc()).limit(5)))
    return {
        "valeurs": len(rows),
        "terminees": sum(1 for r in rows if r.done),
        "en_cours": [
            {
                "isin": r.isin,
                "pages_lues": max((r.next_page or 1) - 1, 0),
                "sujets_vus": len(r.seen or []),
                "plus_ancienne_activite": r.oldest_activity,
                "pause_jusqu_au": r.blocked_until,
            }
            for r in rows
            if not r.done
        ],  # fmt: skip
        "derniers_passages": [
            {"debut": j.started_at, "statut": j.status, "note": (j.note or j.error or "")[:160]} for j in runs
        ],
    }
