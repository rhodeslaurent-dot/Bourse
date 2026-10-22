"""Investment thesis of a proposal (docs/22 §22.8, lot 2.4). Python builds numbered facts from
the proposal, its snapshot and the stored context; Claude writes 3–5 arguments, each tied to fact
ids; Python rejects any number absent from the facts, any unknown fact id, any argument without a
fact. The thesis is stored on the proposal; the snapshot is never modified (rule 10)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Instrument, LetterOpinion, NewsItem, Proposal, TraderMemory
from app.services.assistant import unsupported_numbers

log = logging.getLogger("bourse.thesis")

ACCOUNT_LABELS = {"pea": "PEA", "cto_cash": "CTO comptant", "cto_srd": "CTO SRD"}


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "indisponible"
    if pct:
        return f"{v * 100:.1f} %"
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def facts_for(s: Session, p: Proposal, now: datetime | None = None) -> list[dict[str, str]]:
    """Numbered facts (F1…Fn) — the only material the LLM may use."""
    now = now or datetime.now(UTC)
    d = p.details or {}
    inst = s.get(Instrument, p.isin)
    facts: list[str] = []
    if inst:
        facts.append(
            f"Valeur : {inst.name} ({p.isin}), place {inst.mic}, devise {inst.currency}, secteur "
            f"{inst.sector or 'inconnu'}, capitalisation {_fmt(inst.market_cap_eur)} €"
        )
    det = {
        "d2_breakout": "cassure du pivot 60 séances",
        "d3_range_expansion": "expansion de range",
        "d4_pullback": "repli sur leader",
        "external": "configuration publiée par une lettre",
    }
    facts.append(f"Configuration : {det.get(_detector(s, p), _detector(s, p))}, horizon {p.horizon}")
    entree = d.get("entree") or {}
    facts.append(
        f"Entrée : déclencheur {_fmt(entree.get('declencheur'))}, ordre limite au prix maximal {_fmt(d.get('p_max'))} "
        f"(ratio gain/risque d'au moins 2 net de frais)"
    )
    facts.append(f"Stop : {_fmt(p.stop)} ; objectifs : {', '.join(_fmt(t) for t in (p.targets or [])) or 'aucun'}")
    acc = (d.get("comptes") or {}).get(p.account_recommended or "", {})
    if acc:
        facts.append(
            f"Compte conseillé {ACCOUNT_LABELS.get(p.account_recommended or '', p.account_recommended)} : "
            f"{acc.get('titres')} titres pour {_fmt(acc.get('montant_eur'))} €, "
            f"risque {_fmt(acc.get('risque_eur'))} €, "
            f"gain net à l'objectif {_fmt(acc.get('gain_net_eur'))} € ({_fmt(acc.get('gain_net_pct'), True)}), "
            f"perte nette au stop {_fmt(acc.get('perte_nette_eur'))} € ({_fmt(acc.get('perte_nette_pct'), True)}), "
            f"ratio net {_fmt(acc.get('ratio_net'))}"
        )
    sc = d.get("score") or {}
    if sc:
        facts.append(
            f"Score {_fmt(sc.get('total'))}/100 (atteignable {sc.get('atteignable')}), ratio d'admission "
            f"{_fmt(sc.get('ratio_admission'))}, confiance {sc.get('confiance')}/5, risque {sc.get('risque')}"
        )
        facts += [f"Score : {e}" for e in (sc.get("explications") or [])[:8]]
    ev = _evidence(s, p)
    if ev:
        facts.append("Mesures du déclencheur : " + ", ".join(f"{k} {_fmt(v)}" for k, v in ev.items() if v is not None))
    for z in (d.get("zones") or [])[-4:]:
        facts.append(f"Zone {z.get('type')} {_fmt(z.get('bas'))}–{_fmt(z.get('haut'))} ({z.get('contacts')} contacts)")
    cm = d.get("contexte_marche") or {}
    if cm:
        facts.append(
            f"Marché : risque conjoncturel {cm.get('risque_conjoncturel')}, portail {cm.get('portail')} "
            f"({cm.get('raison_portail')}) ; {cm.get('publication_lettres')}"
        )
    for a in d.get("annonces") or []:
        facts.append(f"Annonce : {a.get('devise')} {a.get('titre')} le {str(a.get('ts'))[:16]} — {a.get('exposition')}")
    if inst and inst.earnings_next_date:
        facts.append(f"Prochains résultats : {inst.earnings_next_date:%d/%m/%Y} (source {inst.earnings_source or '?'})")
    hist = d.get("historique") or {}
    if hist:
        facts.append(
            f"Historique de propositions comparables : {hist.get('n')} cas, espérance rétrécie "
            f"{_fmt(hist.get('shrunk_r'))} R, verdict {hist.get('verdict')}"
        )
    since = now - timedelta(days=7)
    for n in s.scalars(select(NewsItem).where(NewsItem.fetched_at >= since).order_by(NewsItem.fetched_at.desc())):
        if p.isin in (n.isins or []):
            c = n.classification or {}
            facts.append(
                f"Nouvelle du {(n.published_at or n.fetched_at):%d/%m} ({n.source}) : {n.title} — "
                f"{c.get('resume') or ''} [{c.get('direction')}, {c.get('magnitude')}]"
            )
            if len(facts) > 40:
                break
    for o in s.scalars(
        select(LetterOpinion).where(LetterOpinion.isin == p.isin, LetterOpinion.day >= since.date()).limit(5)
    ):
        facts.append(f"Lettre {o.source} du {o.day:%d/%m} ({o.stance}) : {o.summary or ''} « {o.quote or ''} »")
    fd = d.get("fondamentaux") or {}
    if fd.get("objectif_analystes") is not None:
        facts.append(
            f"Consensus des analystes ({fd.get('source')}, {fd.get('jour')}) : "
            f"objectif moyen {_fmt(fd.get('objectif_analystes'))} "
            f"{fd.get('devise') or ''} (fourchette {_fmt(fd.get('objectif_bas'))}–{_fmt(fd.get('objectif_haut'))}), "
            f"{fd.get('nb_analystes')} analystes, recommandation {fd.get('recommandation')}"
        )
    from app.services.press_review import macro_context

    for a in macro_context(s, now, 3, 3):
        facts.append(f"Contexte macro ({a['editeur']}, {a['date'][:10]}) : {a['titre']} — {a.get('resume') or ''}")
    blocked = (p.gates or {}).get("blocked") or []
    facts += [f"Règle : {b}" for b in blocked[:5]]
    facts.append("Fondamentaux : non attribués (aucune source gratuite qualifiée)")
    for m in s.scalars(
        select(TraderMemory).where(TraderMemory.active.is_(True)).order_by(TraderMemory.id.desc()).limit(3)
    ):
        facts.append(f"Leçon retenue par l'utilisateur : {m.text[:200]}")
    return [{"id": f"F{i + 1}", "texte": t} for i, t in enumerate(facts)]


def _detector(s: Session, p: Proposal) -> str:
    from app.db.models import Signal

    sig = s.get(Signal, p.signal_id)
    return sig.detector if sig else "?"


def _evidence(s: Session, p: Proposal) -> dict[str, Any]:
    from app.db.models import Signal

    sig = s.get(Signal, p.signal_id)
    ev = dict((sig.evidence or {}) if sig else {})
    return {k: v for k, v in ev.items() if isinstance(v, int | float) and k not in ("item_id",)}


def write_thesis(s: Session, gateway: Any, p: Proposal, now: datetime | None = None) -> dict[str, Any]:
    """Write and store the thesis of ``p``. Without the API the facts are stored (rules fallback)."""
    from app.llm.gateway import LlmUnavailable
    from app.llm.schemas import ThesisOut

    facts = facts_for(s, p, now)
    pack = {"faits": facts}
    out: dict[str, Any] = {"faits": facts, "statut": "regles", "ecartes": []}
    if gateway is None or not getattr(gateway, "enabled", False):
        out["motif"] = "IA non configurée : faits seuls"
        p.thesis = out
        return out
    user = "<faits>\n" + json.dumps(facts, ensure_ascii=False, indent=1) + "\n</faits>"
    try:
        res, usage = gateway.parse(
            s, "thesis", "thesis", user, ThesisOut, model=gateway.settings.write_model, max_tokens=1500
        )
    except LlmUnavailable as exc:
        out["motif"] = f"IA indisponible : {exc}"
        p.thesis = out
        return out
    ids = {f["id"] for f in facts}
    kept, rejected = [], []
    for a in res.arguments:
        used = [x for x in a.faits if x in ids]
        bad = unsupported_numbers(a.texte, pack)
        if not used:
            rejected.append({"element": a.texte[:80], "motif": "argument sans fait du dossier"})
        elif bad:
            rejected.append({"element": a.texte[:80], "motif": "chiffres absents des faits : " + ", ".join(bad[:4])})
        else:
            kept.append({"texte": a.texte, "faits": used, "nature": a.nature})
    risks = []
    for r in res.risques:
        bad = unsupported_numbers(r, pack)
        (rejected.append({"element": r[:80], "motif": "chiffres absents des faits"}) if bad else risks.append(r))

    def _clean(text: str, label: str) -> str:
        bad = unsupported_numbers(text, pack)
        if bad:
            rejected.append({"element": label, "motif": "chiffres absents des faits : " + ", ".join(bad[:4])})
            return ""
        return text

    out.update(
        {
            "statut": "ok",
            "arguments": kept,
            "risques": risks,
            "pourquoi_maintenant": _clean(res.pourquoi_maintenant, "pourquoi maintenant"),
            "incertitudes": _clean(res.incertitudes, "incertitudes"),
            "ce_qui_invaliderait": _clean(res.ce_qui_invaliderait, "invalidation"),
            "ecartes": rejected,
            "modele": gateway.settings.write_model,
            "cout_usd": usage.cost_usd,
            "redige_le": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        }
    )
    p.thesis = out
    return out


def write_top_theses(s: Session, config: Any, gateway: Any, proposals: list[Proposal], limit: int) -> int:
    n = 0
    for p in proposals:
        if n >= limit:
            break
        if p.rank is None or p.thesis:
            continue
        try:
            write_thesis(s, gateway, p)
            n += 1
        except Exception as exc:  # noqa: BLE001 — a thesis failure never blocks the proposals
            log.warning("thèse %s en échec : %s", p.id, exc.__class__.__name__)
    return n
