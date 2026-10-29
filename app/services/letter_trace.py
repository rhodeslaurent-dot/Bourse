"""What the tool made of the letters of one day (user question of 29/10/2026: « la lettre préconise Viridien,
pourquoi n'est-elle ni dans la watchlist ni dans les opportunités ? »).

For each value read in each letter: its ISIN, direction and levels, whether the engine could recalculate it
(R15: stop, target and a buy or support level), and the proposal it led to — or why none. Then the opinions of
the letters review and the proposal each one has. Reads only; the letter's text is never printed."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LetterOpinion, NewsletterItem, NewsletterValue, Proposal, Signal, WatchlistRow
from app.domain.timeutil import PARIS


def recalculable(levels: dict | None) -> bool:
    lv = levels or {}
    return (
        lv.get("stop") is not None
        and lv.get("objectif") is not None
        and (lv.get("achat") is not None or lv.get("support") is not None)
    )


def _why_not_testable(levels: dict | None) -> str:
    lv = levels or {}
    missing = [k for k in ("stop", "objectif") if lv.get(k) is None]
    if lv.get("achat") is None and lv.get("support") is None:
        missing.append("achat ou support")
    return "niveaux manquants : " + ", ".join(missing)


def _proposal_line(p: Proposal) -> str:
    blocked = [str(b) for b in (p.gates or {}).get("blocked") or []]
    tail = f" — {blocked[-1][:140]}" if blocked else ""
    flags = " · à vérifier" if p.to_verify else ""
    return f"proposition n° {p.id} : {p.action}{flags}{tail}"


def trace(s: Session, day: date) -> list[str]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS)
    end = start + timedelta(days=1)
    items = list(
        s.scalars(
            select(NewsletterItem)
            .where(NewsletterItem.received_at >= start, NewsletterItem.received_at < end)
            .order_by(NewsletterItem.received_at)
        )
    )
    props = list(s.scalars(select(Proposal).where(Proposal.created_at >= start, Proposal.deleted_at.is_(None))))
    by_isin: dict[str, list[Proposal]] = {}
    for p in props:
        by_isin.setdefault(p.isin, []).append(p)
    sigs = {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props] or [-1])))}
    watched = {w.isin for w in s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True)))}
    out = [f"Lettres reçues le {day:%d/%m/%Y} : {len(items)}"]
    for it in items:
        out.append(f"· {it.source} {it.received_at.astimezone(PARIS):%H:%M} Paris — « {it.subject[:90]} »")
        values = list(s.scalars(select(NewsletterValue).where(NewsletterValue.item_id == it.id)))
        if not values:
            out.append("    aucune valeur extraite de cette lettre")
        for v in values:
            lv = v.levels or {}
            shown = ", ".join(f"{k} {lv[k]}" for k in sorted(lv) if isinstance(lv[k], int | float)) or "aucun niveau"
            head = f"    {v.name or '?'} ({v.isin or 'ISIN non reconnu'}) · {v.direction} · {shown}"
            out.append(head)
            if not v.isin:
                out.append("      → aucune proposition : valeur non reconnue (nom absent du référentiel)")
                continue
            if v.direction not in ("buy", "watch"):
                out.append("      → aucune proposition : simple mention ou avis de vente (docs/20 §20.1)")
                continue
            mine = by_isin.get(v.isin, [])
            if recalculable(lv):
                eng = [p for p in mine if (sigs.get(p.signal_id) and sigs[p.signal_id].source == "moteur_lettre")]
                if eng:
                    out += [f"      → recalculée par le moteur, {_proposal_line(p)}" for p in eng]
                else:
                    out.append(
                        "      → recalculable, mais le moteur n'a créé aucune proposition (valeur inactive, moins de "
                        "60 séances d'historique, ou erreur : voir le journal de imap_poll)"
                    )
            else:
                out.append(
                    f"      → non recalculable ({_why_not_testable(lv)}) : surveillance avec les niveaux de la lettre"
                )
                linked = [p for p in mine if p.signal_id == v.signal_id] or mine
                out += [f"        {_proposal_line(p)}" for p in linked] or ["        aucune proposition trouvée"]
            out.append(f"      watchlist : {'oui' if v.isin in watched else 'non'}")
    ops = list(s.scalars(select(LetterOpinion).where(LetterOpinion.day == day)))
    if ops:
        out.append("Revue des lettres (avis relevés) :")
    for op in ops:
        mine = by_isin.get(op.isin or "", [])
        what = "; ".join(_proposal_line(p) for p in mine) or "aucune proposition du jour"
        rec = "recommandation explicite" if op.explicit else "avis"
        out.append(f"  {op.name} ({op.isin or 'ISIN non reconnu'}) · {rec} {op.action} · {op.stance} → {what}")
    return out
