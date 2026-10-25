"""``weak_signals`` (06:40 Paris, market days): Wikidata mapping of new values, Wikipedia views of the
previous days, then the radar labels of the day (docs/26). A source failing leaves the labels computed
on the data available, and says so; never an estimate."""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.weak_signals import compute_hits, refresh_articles, refresh_attention, settings

log = logging.getLogger("bourse.jobs.weak_signals")


def run(ctx: JobContext) -> int:
    st = settings(ctx.config)
    if not st.enabled:
        ctx.note = "radar désactivé (weak_signals.enabled)"
        return 0
    notes: list[str] = []
    with httpx.Client(follow_redirects=True) as client, db_session() as s:
        try:
            with s.begin_nested():
                notes.append(f"{refresh_articles(s, ctx.config, client)} correspondance(s) Wikidata vérifiée(s)")
        except Exception as exc:  # noqa: BLE001 — the views of the articles already known are still read
            log.warning("Wikidata : %s", exc)
            notes.append(f"Wikidata en échec ({exc.__class__.__name__})")
        try:
            # the views of day D are published during D+1 (UTC): read up to the previous day
            rows, errors = refresh_attention(s, ctx.config, client, ctx.run_date - timedelta(days=1))
            notes.append(
                f"{rows} jour(s) de consultations" + (f", {len(errors)} article(s) en échec" if errors else "")
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Wikimedia : %s", exc)
            notes.append(f"Wikimedia en échec ({exc.__class__.__name__})")
        hits = compute_hits(s, ctx.config, ctx.run_date - timedelta(days=1))
        by: dict[str, int] = {}
        for h in hits:
            by[h.label] = by.get(h.label, 0) + 1
        notes.append("étiquettes : " + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "aucune"))
    ctx.note = " ; ".join(notes)
    return len(hits)
