"""``weak_signals`` (06:40 Paris, market days): Wikidata mapping of new values, Wikipedia views of the
previous days, then the radar labels of the day (docs/26). A source failing leaves the labels computed
on the data available, and says so; never an estimate."""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
from sqlalchemy import select

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
        from app.db.models import AttentionDaily
        from app.services.social import backfill, collect_all, followed_values, social_params

        notes += collect_all(s, ctx.config, client, ctx.run_date)  # docs/26 S3: forums and social networks
        n_back = int((social_params(ctx.config).get("watch") or {}).get("backfill_values", 60))
        known = {
            r for r in s.scalars(select(AttentionDaily.isin).where(AttentionDaily.source != "wikipedia").distinct())
        }
        missing = [i.isin for i in followed_values(s, n_back, ctx.run_date) if i.isin not in known][:20]
        if missing:  # a baseline from day one, 20 values per morning at most (politeness)
            try:
                with s.begin_nested():
                    notes += backfill(s, ctx.config, client, ctx.run_date, missing)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"rattrapage en échec ({exc.__class__.__name__})")
        notes += positions_content(s, ctx, client)  # docs/30 S4a: a negative rumour about a held value
        hits = compute_hits(s, ctx.config, ctx.run_date - timedelta(days=1))
        by: dict[str, int] = {}
        for h in hits:
            by[h.label] = by.get(h.label, 0) + 1
        notes.append("étiquettes : " + (", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "aucune"))
    ctx.note = " ; ".join(notes)
    return len(hits)


def positions_content(s, ctx: JobContext, client: httpx.Client) -> list[str]:  # type: ignore[no-untyped-def]
    """Once a day, the forum messages of the open positions: a strong negative theme becomes a P2 caution
    (« rumeur négative, à surveiller ») — a P1 only when the press confirms it (news flow, docs/30 §30.7)."""
    from app.db.models import Instrument, PositionRow
    from app.services.social_content import analyse, content_params, facts

    if not content_params(ctx.config).get("enabled"):
        return []
    isins = list(
        dict.fromkeys(
            s.scalars(select(PositionRow.isin).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
        )
    )
    n = 0
    for isin in isins:
        inst = s.get(Instrument, isin)
        if inst is None:
            continue
        try:
            with s.begin_nested():
                row = analyse(s, ctx.config, client, inst, "position", ctx.llm)
        except Exception as exc:  # noqa: BLE001
            log.warning("contenu %s : %s", isin, exc.__class__.__name__)
            continue
        if row is None:
            continue
        n += 1
        d = row.data or {}
        if d.get("solidite") == "fort" and d.get("direction") == "negatif" and ctx.alerts is not None:
            from app.db.models import NewsItem
            from app.notify.base import Event
            from app.services.instruments import name_of

            since = row.computed_at - timedelta(hours=48)
            news = s.scalars(select(NewsItem).where(NewsItem.fetched_at >= since))
            published = sum(1 for x in news if isin in (x.isins or []))
            ctx.alerts.emit(
                s,
                Event(
                    "P2",
                    "forum_rumeur_negative",
                    f"Position {name_of(isin)} : rumeur négative sur le forum",
                    [
                        *facts(row)[:4],
                        f"{published} publication(s) rattachée(s) sur 48 h"
                        if published
                        else "Aucune publication rattachée sur 48 h : à surveiller, pas un signal de vente",
                        "Valeur : /valeur/" + isin,
                    ],
                    isin=isin,
                ),  # fmt: skip
                ctx.mode,
            )
    return [f"contenu du forum lu pour {n} position(s)"] if isins else []
