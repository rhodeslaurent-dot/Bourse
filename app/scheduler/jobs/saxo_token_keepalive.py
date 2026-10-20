"""``saxo_token_keepalive`` (every 20 min, every day): refresh the Saxo token so the rotating refresh
token never expires while the app runs (ADR-007). An expired authorisation is reported once a day
(P3 ``saxo_auth_expired``) with the command to run; the job itself never fails on it."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from app.data.base import ProviderError
from app.data.providers.saxo import SaxoAuthExpired, SaxoClient
from app.scheduler.runner import JobContext

log = logging.getLogger("bourse.jobs.saxo_token_keepalive")


def notify_auth_expired(ctx: JobContext, why: str) -> bool:
    """P3 once a day: the user must run ``saxo-auth`` again. Returns True when an alert was sent."""
    if ctx.alerts is None:
        return False
    from app.db.session import db_session
    from app.notify.base import Event

    with db_session() as s:
        return bool(
            ctx.alerts.emit(
                s,
                Event(
                    "P3",
                    "saxo_auth_expired",
                    "Saxo : autorisation à renouveler",
                    [
                        why[:120],
                        "Sur le PC : uv run python -m app.cli saxo-auth",
                        "Sans cela, positions et cash Saxo ne sont plus synchronisés.",
                    ],
                ),
                ctx.mode,
                False,
                datetime.now(UTC),
            )
        )


def run(ctx: JobContext) -> int:
    if not os.environ.get("SAXO_TOKEN_KEY"):
        log.info("Saxo non configuré : maintien du jeton ignoré")
        return 0
    client = SaxoClient()
    try:
        info = client.keepalive()
    except SaxoAuthExpired as exc:
        log.warning("%s", exc)
        notify_auth_expired(ctx, str(exc))
        return 0
    except ProviderError as exc:
        if "aucun jeton" in str(exc):
            log.info("%s", exc)
            return 0
        raise
    exp = info.get("refresh_expires_at")
    log.info(
        "jeton Saxo rafraîchi ; refresh token valable jusqu'à %s UTC", datetime.fromtimestamp(exp, UTC) if exp else "?"
    )
    return 1
