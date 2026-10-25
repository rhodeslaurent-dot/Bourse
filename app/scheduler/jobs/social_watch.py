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
        contents = read_contents(s, ctx, client, [h.isin for h in hits])  # docs/30: what is it about?
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
                    *[f"Forum : {x}" for x in contents.get(h.isin, [])[:3]],
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
    ctx.note = f"{len(hits)} étiquette(s) nouvelle(s), {created} proposition(s) préparée(s)" + (
        f", contenu lu pour {len(contents)} valeur(s)" if contents else ""
    )
    return len(hits)


def read_contents(s, ctx: JobContext, client: httpx.Client, isins: list[str]) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    """docs/30 S4a — the messages of the flagged values are read and labelled before the targeted scan, so
    that the card says what the chatter is about (and a pump carried by few accounts blocks the entry)."""
    from app.db.models import Instrument
    from app.services.social_content import analyse, facts

    out: dict[str, list[str]] = {}
    for isin in dict.fromkeys(isins):
        inst = s.get(Instrument, isin)
        if inst is None:
            continue
        try:
            with s.begin_nested():
                row = analyse(s, ctx.config, client, inst, "radar", ctx.llm)
        except Exception as exc:  # noqa: BLE001 — the alert and the scan go on without the content
            log.warning("contenu %s : %s", isin, exc.__class__.__name__)
            continue
        if row is not None:
            lines = [f for f in facts(row) if not f.startswith("résumé")][1:3] or facts(row)[:1]
            if row.effect == "emballement":
                lines = ["peu de comptes ou messages promotionnels : entrée BLOQUÉE (emballement)", *lines[:1]]
            out[isin] = lines
    return out
