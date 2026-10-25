"""``social_watch`` (every 20 min, 07:00–22:40 Paris, every day): the intraday radar of forums and social
networks (docs/26 S3). A value whose retail chatter accelerates before any publication and before the
price gets a « buzz précoce » label, an immediate alert and a targeted engine pass (detector D8) that
prepares a sized opportunity card — never an order."""

from __future__ import annotations

import logging

import httpx

from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.notify.base import Event
from app.scheduler.runner import JobContext
from app.services.instruments import name_of
from app.services.social import watch

log = logging.getLogger("bourse.jobs.social_watch")


def run(ctx: JobContext) -> int:
    from datetime import UTC, datetime

    with httpx.Client(follow_redirects=True) as client, db_session() as s:
        hits = watch(s, ctx.config, client)
        buzz = [h for h in hits if h.label == "buzz_precoce"]
        created = 0
        if buzz:
            from app.services.engine import enabled, scan
            from app.services.quotes import plausible_quotes

            if enabled(ctx.config):
                isins = [h.isin for h in buzz]
                day = datetime.now(UTC).astimezone(PARIS).date()
                rep = scan(s, ctx.config, day, isins=isins, quotes=plausible_quotes(s, isins, ctx.config),
                           buzz_isins=set(isins))  # fmt: skip
                created = rep.created
        if ctx.alerts is not None:
            for h in hits:
                d = h.details or {}
                lines = [
                    h.text[:200],
                    f"{d.get('messages_recents')} message(s) sur {d.get('fenetre_heures')} h · sources : "
                    + ", ".join(d.get("sources") or []),
                    "Aucune publication rattachée sur 48 h · prix encore peu bougé (5 séances)"
                    if h.label == "buzz_precoce"
                    else "Petite capitalisation, volume anormal, aucune publication : entrée BLOQUÉE",
                    "Fiche et niveaux : /opportunites · valeur : /valeur/" + h.isin,
                    "Pas un ordre : signal faible public, à examiner vite",
                ]
                ctx.alerts.emit(
                    s,
                    Event(
                        "P2",
                        f"radar_{h.label}",
                        f"Radar — {name_of(h.isin)} : {h.label.replace('_', ' ')}",
                        lines,
                        isin=h.isin,
                    ),  # fmt: skip
                    ctx.mode,
                )
    ctx.note = f"{len(hits)} étiquette(s) nouvelle(s), {created} proposition(s) préparée(s)"
    return len(hits)
