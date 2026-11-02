# ruff: noqa: E501 — long French lines
"""Lot R1 (docs/38) — « Revue du jour » : the day's decisions first, built from stored data only.

User request of 02/11/2026: « je sais exactement quoi arbitrer, pourquoi et au final quoi faire ». The page follows
the user's own example (a review written by Claude from the letters):

1. **verdict du jour** in one line, and at most four short reasons;
2. **ce que j'attends de toi**, by urgency (unprotected position, stale quote of a held value, order to post, stop
   to raise, sale, quote to check, question of the tool);
3. **marché** : the indices and the macro indicators with their change, the market gate, the regime, the day's risk;
4. **fiches du jour** : one compact block per idea — what the source says, one decision (``app.domain.review``),
   its main reason, what to do, gross gain and gain / risk per target at the current price;
5. **carnet d'ordres** : the limits to post or ready for a pullback;
6. **portefeuille** : positions, stops, last quote and its date;
7. **sources expertes** : managers and media of the last days (never an entry by themselves: no stop, no target);
8. **sources** used, with their times.

Numbers come from the engine and the database (rule 3); a missing value stays « — » (rule 2). Nothing is sent to an
LLM here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    DecisionSnapshot,
    Instrument,
    MacroPoint,
    MediaMention,
    NewsItem,
    NewsletterItem,
    PriceEod,
    Proposal,
    Signal,
)
from app.domain.review import Idea, day_verdict, fmt_price, idea_verdict, target_rows
from app.domain.timeutil import PARIS, REUNION, from_store
from app.services.instruments import name_of

EXPERT_DAYS = 10  # managers' and media mentions shown (a weekly show, its write-up the Monday after)
STALE_SESSIONS = 2  # a held value whose last quote is older than this many weekdays: ask for it


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN → None


def _targets(p: Proposal) -> tuple[float, ...]:
    out: list[float] = []
    for t in p.targets or []:
        v = _f(t.get("niveau") if isinstance(t, dict) else t)
        if v is not None:
            out.append(v)
    return tuple(out)


def _weekdays_between(a: date, b: date) -> int:
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += int(d.weekday() < 5)
    return n


def _source_text(p: Proposal, sig: Signal | None) -> tuple[str, list[str], str | None]:
    """(who, what the source wrote as levels, its quotation) — a letter, else the detector."""
    d = p.details or {}
    lt = d.get("lettre") or {}
    if lt:
        from app.services.letters_verdict import LEVEL_LABELS

        sens = {"buy": "achat", "watch": "à surveiller"}.get(lt.get("sens"), lt.get("sens") or "—")
        levels = [
            f"{LEVEL_LABELS.get(k, k)} {v:.4g}"
            for k, v in (lt.get("niveaux") or {}).items()
            if isinstance(v, int | float)
        ]
        return f"{lt.get('source') or 'lettre'} · {sens}", levels, lt.get("citation")
    if sig is not None:
        return f"détecteur {sig.detector}", [], None
    return "outil", [], None


def idea_from(p: Proposal, quote: Mapping[str, Any] | None) -> Idea:
    """The review's view of one proposal (the engine's stored output and the quote of its snapshot)."""
    d = p.details or {}
    acc = (d.get("comptes") or {}).get(p.account_recommended or "", {})
    ent = d.get("entree") or {}
    levels = (d.get("lettre") or {}).get("niveaux") or {}
    return Idea(
        action=p.action, blocked=tuple(str(b) for b in (p.gates or {}).get("blocked") or []),
        to_verify=bool(p.to_verify), price=_f((quote or {}).get("last")), p_max=_f(d.get("p_max")), stop=_f(p.stop),
        targets=_targets(p), letter_trigger=_f(levels.get("declencheur")), shares=acc.get("titres"),
        plage_threshold=_f(ent.get("seuil")) if ent.get("type") == "ordre à plage de déclenchement" else None,
        expiry=ent.get("jusqu_au") or (d.get("validite") or {}).get("jusqu_au"),
    )  # fmt: skip


def _ideas(s: Session, day: date) -> list[dict[str, Any]]:
    from app.services.engine import todays_ranked

    props = todays_ranked(s, day)
    sigs = (
        {x.id: x for x in s.scalars(select(Signal).where(Signal.id.in_([p.signal_id for p in props])))} if props else {}
    )
    snaps = (
        {
            x.proposal_id: x
            for x in s.scalars(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id.in_([p.id for p in props])))
        }
        if props
        else {}
    )
    insts = (
        {i.isin: i for i in s.scalars(select(Instrument).where(Instrument.isin.in_({p.isin for p in props})))}
        if props
        else {}
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in props:  # the engine's order (rank, then the rest); one block per value (its best-ranked proposal)
        if p.isin in seen or p.action not in ("BUY", "WATCH", "NO_TRADE"):
            continue
        seen.add(p.isin)
        snap = snaps.get(p.id)
        quote = ((snap.quotes or {}).get(p.isin) if snap is not None else None) or {}
        idea = idea_from(p, quote)
        price, seuil, targets = idea.price, idea.plage_threshold, idea.targets
        v = idea_verdict(idea)
        who, written, citation = _source_text(p, sigs.get(p.signal_id))
        inst = insts.get(p.isin)
        out.append({
            "proposal_id": p.id, "isin": p.isin, "nom": name_of(p.isin), "devise": inst.currency if inst else None,
            "pea": bool(inst.pea_eligible) if inst is not None and inst.pea_eligible is not None else None,
            "source": who, "niveaux_source": written, "citation": citation,
            "decision": v.decision, "libelle": v.label, "ton": v.tone, "raison": v.reason, "que_faire": v.todo,
            "cours": price, "cours_statut": quote.get("data_status"), "p_max": idea.p_max, "stop": idea.stop,
            "titres": idea.shares, "compte": p.account_recommended, "expire": idea.expiry, "seuil": seuil,
            "objectifs": target_rows(price, idea.stop, targets),
        })  # fmt: skip
    return out


def _market(s: Session, config: LoadedConfig, day: date) -> dict[str, Any]:
    from app.services.macro import LABELS, market_context

    rows: list[dict[str, Any]] = []
    idx = (config.params.regime or {}).get("index_isins") or {}
    for label, isin in idx.items():
        bars = list(
            s.scalars(
                select(PriceEod)
                .where(PriceEod.isin == isin, PriceEod.day <= day)
                .order_by(PriceEod.day.desc())
                .limit(6)
            )
        )
        if not bars:
            continue
        c = bars[0]
        rows.append({
            "indicateur": str(label).upper(), "valeur": c.close, "jour": c.day,
            "var_1": c.close / bars[1].close - 1 if len(bars) > 1 and bars[1].close else None,
            "var_5": c.close / bars[-1].close - 1 if len(bars) == 6 and bars[-1].close else None,
        })  # fmt: skip
    ctxm = market_context(s, config, day)
    for key, pt in (ctxm.values or {}).items():
        prev = s.scalar(
            select(MacroPoint)
            .where(MacroPoint.indicator == key, MacroPoint.day < pt.day)
            .order_by(MacroPoint.day.desc())
            .offset(4)
            .limit(1)
        )
        rows.append({
            "indicateur": LABELS.get(key, key), "valeur": pt.value, "jour": pt.day, "var_1": None,
            "var_5": (pt.value / prev.value - 1) if prev is not None and prev.value else None,
            "precedent": prev.value if prev is not None else None, "precedent_jour": prev.day if prev is not None else None,
            "source": pt.source,
        })  # fmt: skip
    from app.services.risk_level import today as level_today

    lv = level_today(s, day)
    return {
        "lignes": rows,
        "regime": ctxm.regime,
        "portail": ctxm.gate,
        "portail_raison": ctxm.gate_reason,
        "conjoncture": ctxm.level,
        "niveau_risque": lv.applied if lv is not None else None,
        "niveau_risque_pourquoi": (lv.explanation or [])[1:3] if lv is not None else [],
    }


def _portfolio(s: Session, config: LoadedConfig, day: date, now: datetime) -> list[dict[str, Any]]:
    from app.services.portfolio_review import review_lines

    out = []
    for ln in review_lines(s, config, day, now):
        last = s.scalar(select(func.max(PriceEod.day)).where(PriceEod.isin == ln["isin"], PriceEod.day <= day))
        stale = last is None or _weekdays_between(last, day) > STALE_SESSIONS
        out.append({**ln, "dernier_cours_jour": last, "cours_perime": stale})
    return out


def _experts(s: Session, day: date, today_isins: set[str], held: set[str]) -> list[dict[str, Any]]:
    since = datetime.combine(day - timedelta(days=EXPERT_DAYS), datetime.min.time(), tzinfo=PARIS).astimezone(UTC)
    rows = list(
        s.scalars(
            select(MediaMention).where(MediaMention.published_at >= since).order_by(MediaMention.published_at.desc())
        )
    )
    insts = (
        {i.isin: i for i in s.scalars(select(Instrument).where(Instrument.isin.in_({r.isin for r in rows if r.isin})))}
        if rows
        else {}
    )
    out, seen = [], set()
    for r in rows:
        key = (r.source, r.speaker, r.isin or r.name, r.stance)
        if key in seen or r.stance not in ("achat", "vente"):
            continue
        seen.add(key)
        inst = insts.get(r.isin or "")
        if r.stance == "achat":
            treat = "liste de surveillance : un gérant ne publie ni stop ni objectif, donc pas d'entrée par lui-même"
            if r.isin in today_isins:
                treat += " ; confirme la fiche du jour"
        else:
            treat = "signal de contexte" + (" — vous détenez cette valeur : relire son stop" if r.isin in held else "")
        out.append({
            "source": r.source, "qui": r.speaker, "sens": r.stance, "valeur": name_of(r.isin) if r.isin else r.name,
            "isin": r.isin, "pea": bool(inst.pea_eligible) if inst is not None and inst.pea_eligible is not None else None,
            "date": from_store(r.published_at).astimezone(PARIS).date(), "traitement": treat,
        })  # fmt: skip
    return out


EXPERT_LABELS = {
    "cest_votre_argent": "C'est votre argent (émission BFM)",
    "cva_portefeuille_gerants": "C'est votre argent — relevé hebdomadaire des gérants",
}
EXPERT_STATE_DAYS = 14


def _expert_state(s: Session, config: LoadedConfig, day: date) -> list[str]:
    """Lot R3 (docs/38 §38.6): for each managers' source (media « confirmation »), its last item and what came out
    of it — so that an empty « Sources expertes » is explained (no episode, not read yet, read with no value)."""
    from app.services.media import broadcast_day

    feeds = [f for f in (config.params.data.rss_feeds or []) if isinstance(f, dict)]
    feeds = [f for f in feeds if f.get("kind") == "media_conseil" and f.get("usage") == "confirmation"
             and f.get("url") and "renseigner" not in str(f.get("url"))]  # fmt: skip
    since = datetime.combine(day - timedelta(days=EXPERT_STATE_DAYS), datetime.min.time(), tzinfo=PARIS)
    out: list[str] = []
    for f in feeds:
        name = str(f.get("name"))
        label = EXPERT_LABELS.get(name, name.replace("_", " "))
        rows = list(
            s.scalars(select(NewsItem).where(NewsItem.source == name, NewsItem.fetched_at >= since.astimezone(UTC)))
        )
        if not rows:
            out.append(f"{label} : aucun épisode ni relevé reçu depuis {EXPERT_STATE_DAYS} jours")
            continue
        last = max(rows, key=lambda r: from_store(r.published_at or r.fetched_at))
        pub = from_store(last.published_at or last.fetched_at)
        shown = broadcast_day(last.title, pub)
        when = f"émission du {shown:%d/%m}" if shown else f"publié le {pub.astimezone(PARIS):%d/%m}"
        cls = last.classification or {}
        if "media" not in cls:
            why = cls.get("media_erreur") or "IA indisponible ou page illisible"
            what = f"pas encore lu ({why[:90]}) : nouvel essai au prochain passage"
        else:
            vals = cls.get("media") or []
            buys = sum(1 for v in vals if v.get("stance") == "achat")
            sells = sum(1 for v in vals if v.get("stance") == "vente")
            what = (
                f"lu : {buys} achat(s), {sells} vente(s) de gérants"
                if vals
                else "lu : aucune valeur citée dans le texte disponible"
            )
        out.append(f"{label} : dernier {'relevé' if f.get('parser') == 'gerants' else 'épisode'} ({when}) {what}")
    return out


def _sources(s: Session, day: date, now: datetime) -> list[str]:
    since = now - timedelta(hours=30)
    out = []
    for src, ts in s.execute(
        select(NewsletterItem.source, func.max(NewsletterItem.received_at))
        .where(NewsletterItem.received_at >= since)
        .group_by(NewsletterItem.source)
    ):
        out.append(f"lettre {src} reçue à {from_store(ts).astimezone(REUNION):%H:%M} Réunion")
    last = s.scalar(select(func.max(PriceEod.day)).where(PriceEod.day <= day))
    if last is not None:
        out.append(f"cours de clôture jusqu'au {last:%d/%m/%Y}")
    return out


def build(s: Session, config: LoadedConfig, day: date, now: datetime | None = None) -> dict[str, Any]:
    from app.services.circuit import circuit_state
    from app.services.macro import upcoming_events
    from app.services.risk_view import build_risk_view

    now = now or datetime.now(UTC)
    ideas = _ideas(s, day)
    market = _market(s, config, day)
    try:
        with s.begin_nested():
            circuit = circuit_state(s, config)
    except Exception:  # noqa: BLE001 — unknown, said so (never a guess)
        circuit = None
    head, reasons = day_verdict(
        [i["decision"] for i in ideas], bool(circuit is not None and circuit.tripped), market["portail"]
    )
    pf = _portfolio(s, config, day, now)
    held = {ln["isin"] for ln in pf}
    asks: list[dict[str, Any]] = []
    view = build_risk_view(s, config)
    for p in view["unprotected"]:
        asks.append(
            {
                "urgence": 1,
                "texte": f"Saisir un stop chez le courtier pour {name_of(p.isin)} ({p.qty - p.qty_protected} titres non protégés)",
                "lien": "/positions",
            }
        )
    for ln in pf:
        if ln["cours_perime"]:
            when = f"{ln['dernier_cours_jour']:%d/%m}" if ln["dernier_cours_jour"] else "jamais"
            asks.append(
                {
                    "urgence": 1,
                    "texte": f"Cours de {ln['nom']} non mis à jour (dernier : {when}) : vérifier la source de cours avant de juger le stop",
                    "lien": "/sante",
                }
            )
    for i in ideas:
        if i["decision"] == "acheter":
            asks.append({"urgence": 2, "texte": f"{i['nom']} : {i['que_faire']}", "lien": "/opportunites"})
    for ln in pf:
        if ln.get("stop_change"):
            asks.append(
                {
                    "urgence": 2,
                    "texte": f"Remonter le stop de {ln['nom']} à {ln['stop_propose']} ({ln.get('stop_raison') or 'règle de sortie'})",
                    "lien": "/positions",
                }
            )
        if ln.get("verdict") in ("VENDRE", "ALLÉGER"):
            asks.append(
                {
                    "urgence": 2,
                    "texte": f"{ln['verdict'].capitalize()} {ln['nom']} — {'; '.join(ln.get('signaux') or [])}",
                    "lien": "/positions",
                }
            )
    for i in ideas:
        if i["decision"] == "a_verifier":
            asks.append(
                {
                    "urgence": 3,
                    "texte": f"{i['nom']} : vérifier le cours en direct (le cours de l'analyse n'est pas assez frais)",
                    "lien": "/opportunites",
                }
            )
    book = [
        {
            "nom": i["nom"],
            "limite": i["p_max"],
            "seuil": i["seuil"],
            "stop": i["stop"],
            "titres": i["titres"],
            "compte": i["compte"],
            "ecart": (i["cours"] / i["p_max"] - 1) if i["cours"] and i["p_max"] else None,
            "statut": "à poser" if i["decision"] == "acheter" else "prête pour un repli",
            "expire": i["expire"],
        }  # fmt: skip
        for i in ideas
        if i["decision"] in ("acheter", "attendre_repli") and i["p_max"]
    ]
    events = [
        f"{e.currency} {e.title} — {from_store(e.ts).astimezone(PARIS):%d/%m %H:%M} Paris"
        for e in upcoming_events(s, now, days=3)
    ]
    return {
        "jour": day.isoformat(),
        "construit_le": now.isoformat(timespec="seconds"),
        "verdict": {"titre": head, "raisons": reasons, "coupe_circuit": bool(circuit is not None and circuit.tripped)},
        "attentes": sorted(asks, key=lambda a: a["urgence"]),
        "marche": market,
        "evenements": events,
        "fiches": ideas,
        "carnet": book,
        "portefeuille": pf,
        "experts": _experts(s, day, {i["isin"] for i in ideas}, held),
        "etat_experts": _expert_state(s, config, day),
        "sources": _sources(s, day, now),
    }


def _pct(x: float | None) -> str:
    return f"{x:+.1%}" if x is not None else "—"


def _num(x: float | None) -> str:
    return fmt_price(x)


def as_text(r: dict[str, Any]) -> str:
    """The review as plain text (e-mail): the same order as the page."""
    out = [f"Revue du {r['jour']}", "", f"VERDICT : {r['verdict']['titre']}"]
    out += [f"  - {x}" for x in r["verdict"]["raisons"]]
    out += ["", "CE QUE J'ATTENDS DE TOI"] + (
        [f"  {a['urgence']}. {a['texte']}" for a in r["attentes"]] or ["  rien aujourd'hui"]
    )
    m = r["marche"]
    out += [
        "",
        "MARCHÉ",
        f"  régime {m['regime'] or '—'} · portail {m['portail'] or '—'} ({m['portail_raison'] or '—'}) · niveau de risque {m['niveau_risque'] or '—'}",
    ]
    out += [
        f"  {x['indicateur']} {_num(x['valeur'])} ({x['jour']:%d/%m}) · séance {_pct(x.get('var_1'))} · 5 séances {_pct(x.get('var_5'))}"
        for x in m["lignes"]
    ]
    out += [f"  à venir : {e}" for e in r["evenements"]]
    out += ["", "FICHES DU JOUR"]
    for i in r["fiches"]:
        out.append(f"  {i['nom']} — {i['libelle']} : {i['raison']}")
        out.append(
            f"    source : {i['source']}" + (f" ({', '.join(i['niveaux_source'])})" if i["niveaux_source"] else "")
        )
        out.append(f"    que faire : {i['que_faire']}")
    if not r["fiches"]:
        out.append("  aucune idée aujourd'hui")
    out += ["", "CARNET D'ORDRES"] + (
        [
            f"  {b['nom']} : limite {_num(b['limite'])}, stop {_num(b['stop'])}, {b['titres'] or '—'} titres, {b['statut']} (écart {_pct(b['ecart'])})"
            for b in r["carnet"]
        ]
        or ["  aucun ordre"]
    )
    out += ["", "PORTEFEUILLE"] + (
        [
            f"  {p['nom']} : {p.get('quantite')} titres, stop {p.get('stop') or '—'}, cours {p.get('cours', '—')} ({p['dernier_cours_jour'] or '—'}) → {p.get('verdict', '—')}"
            for p in r["portefeuille"]
        ]
        or ["  aucune position"]
    )
    if r["experts"] or r.get("etat_experts"):
        out += ["", "SOURCES EXPERTES"] + [
            f"  {e['qui'] or e['source']} : {e['sens']} {e['valeur']} — {e['traitement']}" for e in r["experts"]
        ]
        out += [f"  état : {x}" for x in r.get("etat_experts") or []]
    out += [
        "",
        "Sources : " + ("; ".join(r["sources"]) or "—"),
        "Aucun ordre n'est passé par l'outil : vous décidez chez le courtier.",
    ]
    return "\n".join(out)


def telegram_lines(r: dict[str, Any]) -> list[str]:
    """At most 12 lines (CLAUDE.md): the verdict, the first asks, the first ideas."""
    out = [r["verdict"]["titre"]] + r["verdict"]["raisons"][:2]
    out += [f"À faire : {a['texte']}"[:140] for a in r["attentes"][:3]]
    out += [f"{i['nom']} — {i['libelle']}"[:140] for i in r["fiches"][:4]]
    out.append("Détail : page Revue du jour")
    return out[:12]


__all__ = ["as_text", "build", "idea_from", "telegram_lines"]
