"""``short_positions_refresh`` (every evening): net short positions published by the market authorities
configured in ``data.short_positions.sources`` (docs/23 S2). One authority failing never stops the others."""

from __future__ import annotations

import logging

from app.data.providers.short_positions import fetch
from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.short_positions import store

log = logging.getLogger("bourse.jobs.short_positions")


def run(ctx: JobContext) -> int:
    cfg = (getattr(ctx.config.params.data, "short_positions", None) or {}) if ctx.config.params.data else {}
    sources = [x for x in cfg.get("sources") or [] if x.get("url") and "renseigner" not in str(x.get("url"))]
    total, notes = 0, []
    for src in sources:
        name = str(src.get("authority"))
        try:
            rows, url = fetch(src)
        except Exception as exc:  # noqa: BLE001 — one authority failing never stops the others
            log.warning("positions courtes %s : %s", name, exc)
            notes.append(f"{name} en échec ({str(exc)[:60]})")
            continue
        with db_session() as s:
            n = store(s, name, rows, url)
        total += n
        notes.append(f"{name} {len(rows)} lues, {n} nouvelles")
    ctx.note = " ; ".join(notes) or "aucune autorité configurée (data.short_positions.sources)"
    return total
