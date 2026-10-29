"""``GET /api/cours?isins=…&force=0|1`` — live quotes for display (opportunities, watchlist), user request of
28/10/2026. Read-only: Saxo (15 s) then EODHD (15 min, or on a manual refresh within a daily budget). The
distance to each open proposal's maximum entry price is computed here, never in the browser."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.db.models import Proposal
from app.db.session import db_session
from app.domain.timeutil import from_store
from app.services.live_quotes import fmt_pct, service
from app.services.reevaluations import latest_by_proposal, summary

router = APIRouter(prefix="/api")

ISIN_RE = re.compile(r"^[A-Z0-9]{12}$")
MAX_ISINS = 200


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/cours")
def live_quotes(request: Request, isins: str = "", force: int = 0) -> dict[str, Any]:
    wanted = [x for x in dict.fromkeys(i.strip().upper() for i in isins.split(",")) if ISIN_RE.match(x)][:MAX_ISINS]
    cfg = request.app.state.config
    now = _now()
    with db_session() as s:
        res = service().refresh(s, cfg, getattr(request.app.state, "calendars", None), wanted, bool(force), now)
        props = (
            [
                p
                for p in s.scalars(
                    select(Proposal)
                    .where(Proposal.isin.in_(wanted), Proposal.deleted_at.is_(None), Proposal.status == "open")
                    .order_by(Proposal.id.desc())
                    .limit(500)
                )
                if from_store(p.created_at).date() == now.date()
            ]
            if wanted
            else []
        )
        distances: dict[str, dict[str, Any]] = {}
        to_seuil: dict[str, dict[str, Any]] = {}
        for p in props:
            pmax = (p.details or {}).get("p_max")
            last = res.views[p.isin].last if p.isin in res.views else None
            if pmax and last is not None:
                d = last / float(pmax) - 1
                distances[str(p.id)] = {"ecart": d, "ecart_txt": fmt_pct(d)}
                seuil = ((p.details or {}).get("entree") or {}).get("seuil")  # a breakout's plage (docs/06 §6.3)
                if isinstance(seuil, int | float) and seuil > 0:
                    to_seuil[str(p.id)] = {"ecart": last / float(seuil) - 1, "ecart_txt": fmt_pct(last / seuil - 1)}
        reevals = {str(k): summary(v) for k, v in latest_by_proposal(s, [p.id for p in props]).items()}
        message = res.message
        if not message and not res.any_open and wanted:
            message = "places fermées : dernières clôtures affichées"
        elif not message and res.next_poll_s:
            every = f"{res.next_poll_s} s" if res.next_poll_s < 60 else f"{res.next_poll_s // 60} min"
            message = f"actualisation automatique toutes les {every}"
        return {
            "maintenant": now.isoformat(),
            "cours": {i: v.as_json() for i, v in res.views.items()},
            "ecarts": distances,
            "ecarts_seuil": to_seuil,
            "reevaluations": reevals,
            "next_poll_s": res.next_poll_s,
            "eodhd": {
                "utilises": res.eodhd_used,
                "max": res.eodhd_max,
                "restant": res.eodhd_left,
                "epuise": res.budget_exhausted,
            },
            "manuel": {"demande": res.manual_requested, "accepte": res.manual_accepted},
            "message": message,
        }
