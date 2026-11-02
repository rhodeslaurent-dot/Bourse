"""What the tool made of the letters of one day (user question of 29/10/2026: « la lettre préconise Viridien,
pourquoi n'est-elle ni dans la watchlist ni dans les opportunités ? »).

For each value read in each letter: its ISIN, direction and levels, whether they are recalculable in the R15
sense, and the proposal of the day with its verdict (docs/20 §20.10: every recommended value is analysed) — or why
none; the letter's funnel. Then the opinions of
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
    from app.services.letters_verdict import label, sentence

    blocked = [str(b) for b in (p.gates or {}).get("blocked") or []]
    flags = " · à vérifier" if p.to_verify else ""
    if (p.details or {}).get("verdict"):  # docs/20 §20.10: the verdict and its sentence, as on the card
        return f"proposition n° {p.id} : {p.action}{flags} · {label(p)} — {sentence(p)}"
    tail = f" — {blocked[-1][:140]}" if blocked else ""
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
        rx = (it.parsed or {}).get("reextract") or {}
        if rx.get("status") == "ok":  # letters-reextract (30/10/2026): read again with the current prompt
            from app.domain.timeutil import to_reunion

            at = datetime.fromisoformat(rx["at"]) if rx.get("at") else None
            out.append(
                f"    relue le {f'{to_reunion(at):%d/%m/%Y}' if at else '?'} avec la consigne v{rx.get('version')} : "
                f"{rx.get('values', 0)} valeur(s), {rx.get('with_levels', 0)} avec niveaux"
            )
        elif rx.get("status") == "echec":
            out.append(f"    relecture en échec (consigne v{rx.get('version')}) : {rx.get('error', '')}")
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
        fun = (it.parsed or {}).get("funnel")
        if fun:
            from app.services.letter_analysis import funnel_text

            out.append(f"    entonnoir : {funnel_text([fun])}")
            if fun.get("without_proposal"):
                out.append(f"    sans proposition : {', '.join(fun['without_proposal'])}")
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
            r15 = "recalculable (R15)" if recalculable(lv) else f"non recalculable R15 ({_why_not_testable(lv)})"
            out.append(f"      niveaux : {r15} — analysée par l'outil dans tous les cas (docs/20 §20.10)")
            eng = [p for p in mine if (sigs.get(p.signal_id) and sigs[p.signal_id].source == "moteur_lettre")]
            older = [p for p in mine if p.signal_id == v.signal_id]  # bare WATCH of the reception before 30/10/2026
            if eng or older:
                out += [f"      → {_proposal_line(p)}" for p in eng + older]
            else:
                out.append(
                    "      → aucune proposition ce jour : l'analyse n'a pas tourné (voir le journal de imap_poll, "
                    "ou le scan du matin)"
                )
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


def _levels_text(lv: dict | None) -> str:
    from app.domain.review import fmt_price
    from app.services.letters_verdict import LEVEL_LABELS

    order = ("achat", "declencheur", "support", "resistance", "objectif", "objectif_2", "objectif_3", "stop")
    shown = [f"{LEVEL_LABELS.get(k, k)} {fmt_price(float(lv[k]))}" for k in order
             if lv and isinstance(lv.get(k), int | float)]  # fmt: skip
    return ", ".join(shown) or "aucun niveau"


def rules_reread(
    s: Session, day: date, band: tuple[float, float] | None = None, apply: bool = False, now: datetime | None = None
) -> list[str]:
    """Lot R3 (docs/38 §38.6): the letters of ``day`` read again by the current rules — free, no LLM. Per value
    stored: the levels stored and the levels the rules read now (closed list of names, then ``band`` × the value's
    last close). ``apply``: a stored value **without any level** gets the rules' levels; a value that has levels keeps
    them, and a value linked to a proposal keeps its proposal and decision snapshot (rule 10) — the engine's next
    analysis reads the new levels. Values the rules find but the reception did not store are listed, not added. The
    letter's text is never printed."""
    from app.data.providers.imap_gmail import extract_values
    from app.domain.letter_levels import canonical_levels, within_band
    from app.domain.timeutil import from_store
    from app.services.news import alias_index
    from app.services.newsletters import _prev_close, session_day_for

    start = datetime.combine(day, datetime.min.time(), tzinfo=PARIS)
    items = list(
        s.scalars(
            select(NewsletterItem)
            .where(NewsletterItem.received_at >= start, NewsletterItem.received_at < start + timedelta(days=1))
            .order_by(NewsletterItem.received_at)
        )
    )
    aliases = alias_index(s)
    out = [f"Relecture par les règles actuelles (sans IA, gratuite) — lettres du {day:%d/%m/%Y} : {len(items)}"]
    filled = 0
    for it in items:
        body = it.body_text or ""
        received = from_store(it.received_at)
        sday = session_day_for(received)
        out.append(f"· {it.source} {received.astimezone(PARIS):%H:%M} Paris — « {it.subject[:80]} » ({len(body)} car.)")
        read = {v.isin: v for v in extract_values(body, aliases) if v.isin}
        rows = list(
            s.scalars(select(NewsletterValue).where(NewsletterValue.item_id == it.id).order_by(NewsletterValue.id))
        )
        n_fill = 0
        for row in rows:
            v = read.pop(row.isin, None) if row.isin else None
            lv, _ = canonical_levels(v.levels if v is not None else {})
            lv, far = within_band(lv, _prev_close(s, row.isin, sday), band) if row.isin else (lv, [])
            stored = row.levels or {}
            line = f"    {row.name or '?'} ({row.isin or 'ISIN non reconnu'}) · enregistrés : {_levels_text(stored)}"
            line += f" · règles actuelles : {_levels_text(lv)}"
            if far:
                line += f" · écartés (trop loin du cours) : {', '.join(far)}"
            if apply and lv and not stored:
                row.levels = dict(lv)
                n_fill += 1
                line += " → enregistrés"
            out.append(line)
        for v in read.values():
            lv, _ = canonical_levels(v.levels)
            out.append(f"    {v.name} ({v.isin}) · lue par les règles, absente des valeurs enregistrées · "
                       f"{_levels_text(lv)} (non ajoutée)")  # fmt: skip
        if n_fill:
            parsed = dict(it.parsed or {})  # reassigned: the JSON column is not mutation-tracked
            parsed["rules_fill"] = {"at": (now or datetime.now(PARIS)).isoformat(timespec="seconds"), "values": n_fill}
            it.parsed = parsed
            filled += n_fill
    if apply:
        out.append(f"{filled} valeur(s) sans niveau complétée(s) par les règles ; les autres sont inchangées.")
        s.flush()
    else:
        out.append("Aperçu : rien n'est enregistré (ajouter --fill pour compléter les valeurs restées sans niveau).")
    return out
