"""What the tool made of the letters of one day (user question of 29/10/2026: « la lettre préconise Viridien,
pourquoi n'est-elle ni dans la watchlist ni dans les opportunités ? »).

For each value read in each letter: its ISIN, direction and levels, whether the engine could recalculate it
(R15: stop, target and a buy or support level), and the proposal it led to — or why none. Then the opinions of
the letters review and the proposal each one has. Reads only; the letter's text is never printed."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LetterOpinion, NewsletterItem, NewsletterValue, Proposal, Signal, WatchlistRow
from app.domain.letter_levels import recalculable
from app.domain.timeutil import PARIS


def _why_not_testable(levels: dict | None) -> str:
    lv = levels or {}
    missing = [k for k in ("stop", "objectif") if lv.get(k) is None]
    if lv.get("achat") is None and lv.get("support") is None and lv.get("declencheur") is None:
        missing.append("achat, support ou déclencheur")
    return "niveaux manquants : " + ", ".join(missing)


def _proposal_line(p: Proposal) -> str:
    blocked = [str(b) for b in (p.gates or {}).get("blocked") or []]
    tail = f" — {blocked[-1][:140]}" if blocked else ""
    flags = " · à vérifier" if p.to_verify else ""
    return f"proposition n° {p.id} : {p.action}{flags}{tail}"


def llm_answer(s: Session, item: NewsletterItem, model: str, prompts_dir: Path) -> tuple[str, list[dict]] | None:
    """The reader's stored answer for this letter (replay cache, same key as the gateway), whatever the prompt
    version it was read with: (version, values). None when no answer is stored."""
    import hashlib

    from app.db.models import LlmCache

    for f in sorted(prompts_dir.glob("newsletter_extract_v*.md"), reverse=True):
        version, system = f.stem.split("_v")[-1], f.read_text(encoding="utf-8")
        key = f"{model}|newsletter_extract|{version}|{system}|{item.body_text[:12000]}"
        row = s.get(LlmCache, hashlib.sha256(key.encode()).hexdigest())
        if row is not None:
            return version, list((row.response or {}).get("values") or [])
    return None


def trace(s: Session, day: date, reader: tuple[str, Path] | None = None) -> list[str]:
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
        how = {"llm": "lecture par l'IA puis contrôle", "rules": "lecture par règles seules (IA indisponible)"}
        out.append(f"    {how.get(it.parsed_by or '', it.parsed_by or 'lecture inconnue')}")
        if reader is not None:
            ans = llm_answer(s, it, *reader)
            if ans is None:
                out.append("    réponse de l'IA introuvable dans le cache")
            else:
                with_levels = [v for v in ans[1] if v.get("levels")]
                out.append(
                    f"    réponse de l'IA (consigne v{ans[0]}) : {len(ans[1])} valeur(s), "
                    f"{len(with_levels)} avec niveaux"
                )
                for v in ans[1][:30]:
                    lv = ", ".join(f"{k} {x}" for k, x in (v.get("levels") or {}).items()) or "aucun niveau"
                    out.append(f"      IA : {v.get('name')} · {v.get('direction')} · {lv}")
        rejected = (it.parsed or {}).get("rejected_llm_levels") or []
        if rejected:
            out.append(f"    niveaux proposés par l'IA mais absents du texte, rejetés : {', '.join(rejected[:12])}")
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


def reread(s: Session, day: date, gateway: object) -> list[str]:
    """The letters of ``day`` read again with the current extraction prompt (newsletter_extract, latest version):
    the values and levels the reader gives now, the level names after the closed list and whether each number is
    written in the letter. Nothing is stored but the call log and the replay cache; the letter text is never
    printed."""
    from app.domain.letter_levels import canonical_levels
    from app.llm.schemas import NewsletterExtraction
    from app.services.newsletters import _number_in_text

    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS)
    items = s.scalars(
        select(NewsletterItem)
        .where(NewsletterItem.received_at >= start, NewsletterItem.received_at < start + timedelta(days=1))
        .order_by(NewsletterItem.received_at)
    )
    out: list[str] = []
    for it in items:
        body = it.body_text or ""
        out.append(f"· {it.source} — « {it.subject[:80]} » ({len(body)} caractères, {min(len(body), 12000)} lus)")
        ext, usage = gateway.parse(  # type: ignore[attr-defined]
            s, "newsletter_extract", "newsletter_extract", body[:12000], NewsletterExtraction, max_tokens=2048
        )
        out.append(f"    {len(ext.values)} valeur(s), coût {usage.cost_usd:.4f} $")
        for v in ext.values:
            levels, dropped = canonical_levels(v.levels)
            shown = ", ".join(
                f"{k} {x:g}{'' if _number_in_text(x, body) else ' (absent du texte : rejeté)'}"
                for k, x in levels.items()
            )
            extra = f" · noms inconnus ignorés : {', '.join(dropped)}" if dropped else ""
            out.append(f"    {v.name} · {v.direction} · {shown or 'aucun niveau'}{extra}")
    return out
