"""Latest intraday re-evaluation of proposals, worded for the opportunities page (``proposal_reevaluations``)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProposalReevaluation
from app.domain.reevaluation import ENTRY_STATUSES, LABELS, ReevalStatus
from app.domain.timeutil import REUNION, from_store


def latest_by_proposal(s: Session, proposal_ids: list[int]) -> dict[int, ProposalReevaluation]:
    out: dict[int, ProposalReevaluation] = {}
    if not proposal_ids:
        return out
    for r in s.scalars(
        select(ProposalReevaluation)
        .where(ProposalReevaluation.proposal_id.in_(proposal_ids))
        .order_by(ProposalReevaluation.id)
    ):
        out[r.proposal_id] = r
    return out


def summary(r: ProposalReevaluation) -> dict[str, Any]:
    """« Réévaluée à 14:32 (heure Réunion) : prix d'entrée atteint — cours différé 15 min, à vérifier »."""
    at = from_store(r.ts).astimezone(REUNION)
    head = f"Réévaluée à {at:%H:%M} (heure Réunion) : {LABELS.get(r.status, r.status)}"
    reasons = [str(x) for x in (r.reasons or [])]
    tail = ""
    if r.status in ENTRY_STATUSES:
        if r.actionable:
            tail = "cours temps réel, ordre limite possible au prix maximal (jamais au-delà)"
        elif r.data_status == "delayed":
            m = next((re.search(r"différé (\d+) min", x) for x in reasons if "différé" in x), None)
            tail = f"cours différé{' ' + m.group(1) + ' min' if m else ''}, à vérifier"
        else:
            why = [x for x in reasons if x.startswith(("à vérifier", "BLOQUÉ", "proposition"))]
            tail = " · ".join(why) or "à vérifier"
        tone = "green" if r.actionable else "amber"
    elif r.status == ReevalStatus.DEGRADED.value:
        tail, tone = "l'outil n'annule rien : à faire chez le courtier", "red"
    else:
        tone = "slate"
    return {
        "texte": head + (f" — {tail}" if tail else ""),
        "ton": tone,
        "actionnable": bool(r.actionable),
        "statut": r.status,
        "cours": r.price,
        "source": r.source,
        "statut_cours": r.data_status,
    }
