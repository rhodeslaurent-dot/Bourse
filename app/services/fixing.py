# ruff: noqa: E501 — long French card lines
"""« Repli sur support » protocol (docs/06 §6.7, framework §6.1, R6): at ``entry.fixing_card_time``
(17:15 Paris) each open admitted pullback proposal is re-checked with the quote at analysis time
(R19). The support must hold (R5) and the price must be at or under the maximum entry price: then
a card « entrée au fixing de clôture » is sent (limit = P_max, quantity, stop to post at once).
A support given back degrades the proposal (noted, the snapshot is never modified)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Proposal, Signal
from app.domain.letter_setup import TRIGGER_WAIT
from app.services.instruments import name_of


def candidates(s: Session) -> list[tuple[Proposal, Signal]]:
    out = []
    for p in s.scalars(select(Proposal).where(Proposal.status == "open", Proposal.action.in_(("BUY", "WATCH")))):
        d = p.details or {}
        if d.get("admission") not in ("admis", "refuse") or not d.get("p_max"):
            continue
        sig = s.get(Signal, p.signal_id)
        if sig is None or sig.detector not in ("d4_pullback", "external"):
            continue
        if any(TRIGGER_WAIT in b for b in (p.gates or {}).get("blocked", [])):
            continue  # a letter's breakout not crossed: an alert, never a fixing card (R5, R6 — review of 30/10/2026)
        if (d.get("entree") or {}).get("type") == "ordre à plage de déclenchement":
            continue  # a breakout's plage is a stop-limit order at its seuil, not a fixing entry (docs/06 §6.3)
        out.append((p, sig))
    return out


def check(s: Session, config: Any, quotes: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """One line per candidate: ``fixing`` (card to send), ``degradee`` (support lost) or ``attente``."""
    now = now or datetime.now(UTC)
    res = []
    for p, sig in candidates(s):
        d = dict(p.details or {})
        q = quotes.get(p.isin)
        price = getattr(q, "last", None)
        support = (sig.evidence or {}).get("support") or (sig.evidence or {}).get("niveaux", {}).get("support")
        level = float(support) if support else float(p.stop or 0) * 1.0
        line = {"proposal": p, "nom": name_of(p.isin), "cours": price, "p_max": d["p_max"], "statut": "attente"}
        if price is None:
            line["motif"] = "cours indisponible : pas de fiche (règle 2)"
        elif support and price < level:
            line["statut"], line["motif"] = (
                "degradee",
                f"support {level:.4g} perdu ({price:.4g}) : un repli à travers le support n'est pas une entrée (R5)",
            )
            d.setdefault("fixing", []).append(
                {"ts": now.isoformat(timespec="minutes"), "statut": "degradee", "cours": price}
            )
        elif price <= float(d["p_max"]):
            line["statut"], line["motif"] = "fixing", "support tenu et cours sous le prix maximal"
            d.setdefault("fixing", []).append(
                {"ts": now.isoformat(timespec="minutes"), "statut": "fixing", "cours": price}
            )
        else:
            line["motif"] = f"cours {price:.4g} au-dessus du prix maximal {float(d['p_max']):.4g} : limite maintenue"
        p.details = d
        res.append(line)
    s.flush()
    return res


def send_cards(s: Session, alerts: Any, mode: str, lines: list[dict[str, Any]]) -> int:
    from app.notify.base import Event
    from app.notify.telegram import proposal_buttons

    n = 0
    for ln in lines:
        if ln["statut"] != "fixing":
            continue
        p: Proposal = ln["proposal"]
        acc = ((p.details or {}).get("comptes") or {}).get(p.account_recommended or "", {})
        ev = Event(
            "P2",
            "fixing_card",
            f"Entrée au fixing de 17:35 : {ln['nom']}",
            [
                f"Ordre limite {float(ln['p_max']):.4g} au fixing · {acc.get('titres')} titres · {p.account_recommended}",
                f"Cours {ln['cours']:.4g} : support tenu, sous le prix maximal",
                f"Stop {p.stop:.4g} à poser dès l'exécution",
                "Pas un ordre : à saisir vous-même avant 17:30",
            ],
            proposal_buttons(p.id),
            isin=p.isin,
        )
        if alerts.emit(s, ev, mode):
            n += 1
    return n
