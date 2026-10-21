"""Daily letters review (docs/20): Claude reads the day's letters and linked pages with the tool's
context and returns opinions per value and validations; Python checks everything (literal quotes,
numbers present in the dossier, known targets) and derives the recommendations deterministically.

The LLM never computes: no price, level, size or score comes from it. An explicit recommendation
becomes at most a WATCH proposal with the levels written in the letter; risk rules stay in charge.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    Instrument,
    LetterOpinion,
    LetterReview,
    LetterValidation,
    MarketRegimeRow,
    NewsletterItem,
    NewsletterValue,
    PositionRow,
    Proposal,
    Signal,
    WatchlistRow,
)
from app.domain.timeutil import PARIS
from app.services.assistant import unsupported_numbers
from app.services.explain import explain_proposal
from app.services.instruments import name_of
from app.web.labels import label

MAX_LOOKBACK = timedelta(days=4)  # after a weekend or an outage, never re-read more than this


def params_of(config: LoadedConfig) -> Any | None:
    return config.params.letters_review if config.feature_enabled("letters_review") else None


def _norm(text: str) -> str:
    t = (text or "").replace("’", "'").replace(" ", " ").replace("«", '"').replace("»", '"')
    return " ".join(t.lower().split())


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def letters_since(s: Session, now: datetime) -> list[NewsletterItem]:
    """Letters received since the previous successful review (24 h by default, 4 days at most)."""
    last = s.scalar(select(LetterReview.ts).where(LetterReview.status == "ok").order_by(LetterReview.ts.desc()))
    since = max(_aware(last) if last else now - timedelta(hours=24), now - MAX_LOOKBACK)
    rows = s.scalars(select(NewsletterItem).order_by(NewsletterItem.received_at.desc()))
    return [r for r in rows if since < _aware(r.received_at) <= now]


def build_dossier(s: Session, config: LoadedConfig, items: list[NewsletterItem], now: datetime) -> dict[str, Any]:
    p = params_of(config)
    per_letter = p.max_chars_per_letter if p else 8000
    budget = p.max_total_chars if p else 60000
    window = p.window_days if p else 5
    letters = []
    for it in items:  # most recent first
        text = (it.body_text or "")[:per_letter]
        if budget <= 0:
            break
        text = text[:budget]
        budget -= len(text)
        recv = _aware(it.received_at)
        letters.append(
            {
                "id": it.id,
                "source": it.source,
                "objet": it.subject,
                "recue": recv.astimezone(PARIS).strftime("%d/%m/%Y %H:%M Paris"),
                "page_liee": ((it.parsed or {}).get("page_liee") or {}).get("statut"),
                "texte": text,
            }
        )
    since = now - timedelta(days=window)
    proposals = []
    for pr in s.scalars(select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id.desc()).limit(400)):
        if _aware(pr.created_at) < since or pr.status not in ("open", "watch", "taken"):
            continue
        ex = explain_proposal(pr, s.get(Signal, pr.signal_id), None, None)
        proposals.append({"id": pr.id, "isin": pr.isin, "valeur": name_of(pr.isin), "explication": ex.summary})
    positions = [
        {
            "isin": r.isin,
            "valeur": name_of(r.isin),
            "quantite": r.qty_held,
            "protection": label("protection_state", r.protection_state),
            "stop": r.stop_current,
        }
        for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
    ]
    watch = [
        {"isin": w.isin, "valeur": name_of(w.isin), "motif": label("watch_reason", w.reason)}
        for w in s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True)).limit(80))
    ]
    reg = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
    from app.services.learning import memory_items

    return json.loads(
        json.dumps(
            {
                "lettres": letters,
                "contexte": {
                    "propositions": proposals[:60],
                    "positions": positions,
                    "watchlist": watch,
                    "regime": {"jour": reg.day, "regime": label("regime", reg.regime)} if reg else None,
                    "lecons": [m.text for m in memory_items(s, 15)],
                },
            },
            default=str,
            ensure_ascii=False,
        )
    )


def _resolve_isin(s: Session, isin: str | None, name: str) -> str | None:
    if isin and s.get(Instrument, isin.upper()) is not None:
        return isin.upper()
    from app.services.news import alias_index

    return alias_index(s).get((name or "").strip().lower())


def run_review(
    s: Session,
    config: LoadedConfig,
    gateway: Any,
    trigger: str = "manual",
    now: datetime | None = None,
    alerts: Any | None = None,
    mode: str = "reunion",
) -> LetterReview:
    from app.llm.gateway import LlmUnavailable
    from app.llm.schemas import LettersReviewOut

    now = now or datetime.now(UTC)
    p = params_of(config)
    items = letters_since(s, now)
    dossier = build_dossier(s, config, items, now)
    rev = LetterReview(
        ts=now,
        day=now.astimezone(PARIS).date(),
        trigger=trigger,
        status="empty" if not items else "rules",
        item_ids=[i.id for i in items],
        dossier=dossier,
        rejected=[],
    )
    s.add(rev)
    s.flush()
    if not items:
        rev.synthesis = "Aucune lettre reçue depuis la revue précédente."
        return rev
    if gateway is None or not getattr(gateway, "enabled", False):
        rev.synthesis = f"{len(items)} lettre(s) reçue(s) ; revue IA indisponible (clé Anthropic absente)."
        return rev
    user = f"<dossier>\n{json.dumps(dossier, ensure_ascii=False, indent=1)}\n</dossier>"
    try:
        out, usage = gateway.parse(
            s,
            "letters_review",
            "letters_review",
            user,
            LettersReviewOut,
            model=gateway.settings.write_model,
            max_tokens=p.max_tokens if p else 4000,
        )
    except LlmUnavailable as exc:
        rev.status = "unavailable"
        rev.synthesis = f"{len(items)} lettre(s) reçue(s) ; revue IA indisponible ({exc})."
        return rev
    rev.status, rev.cost_usd, rev.model = "ok", usage.cost_usd, gateway.settings.write_model
    rejected: list[dict[str, str]] = []
    texts = {it.id: _norm(it.body_text or "") for it in items}
    sources = {it.id: it.source for it in items}

    bad = unsupported_numbers(out.synthese, dossier)
    rev.synthesis = (
        out.synthese if not bad else f"{len(items)} lettre(s) analysée(s) (synthèse IA écartée : chiffre absent)."
    )
    if bad:
        rejected.append({"element": "synthèse", "motif": "chiffres absents des lettres : " + ", ".join(bad[:5])})

    for a in out.avis:
        ids = [i for i in a.lettres if i in texts] or list(texts)
        quote = _norm(a.citation)
        if not quote or not any(quote in texts[i] for i in ids):
            rejected.append({"element": a.name, "motif": "citation introuvable dans les lettres"})
            continue
        b = unsupported_numbers(" ".join([a.resume, a.catalyseur]), dossier)
        if b:
            rejected.append({"element": a.name, "motif": "chiffres absents des lettres : " + ", ".join(b[:5])})
            continue
        src_ids = [i for i in ids if quote in texts[i]]
        s.add(
            LetterOpinion(
                review_id=rev.id,
                day=rev.day,
                isin=_resolve_isin(s, a.isin, a.name),
                name=a.name[:128],
                source=sources.get(src_ids[0]) if src_ids else None,
                item_ids=src_ids,
                stance=a.orientation,
                explicit=a.explicite,
                action=a.action_lettre,
                catalyst=a.catalyseur or None,
                horizon=a.horizon,
                summary=a.resume,
                quote=a.citation,
            )
        )

    ctx = dossier["contexte"]
    known = {
        "proposition": {str(x["id"]): x["isin"] for x in ctx["propositions"]},
        "position": {x["isin"]: x["isin"] for x in ctx["positions"]},
        "watchlist": {x["isin"]: x["isin"] for x in ctx["watchlist"]},
    }
    target_type = {"proposition": "proposal", "position": "position", "watchlist": "watchlist"}
    for v in out.validations:
        ref = v.reference.strip().upper() if v.cible != "proposition" else v.reference.strip()
        if ref not in known[v.cible]:
            rejected.append({"element": f"{v.cible} {v.reference}", "motif": "cible absente du contexte"})
            continue
        b = unsupported_numbers(v.justification, dossier)
        if b:
            rejected.append({"element": f"{v.cible} {v.reference}", "motif": "chiffres absents : " + ", ".join(b[:5])})
            continue
        s.add(
            LetterValidation(
                review_id=rev.id,
                day=rev.day,
                target_type=target_type[v.cible],
                target_ref=ref,
                isin=known[v.cible][ref],
                verdict=v.verdict,
                justification=v.justification,
                item_ids=[i for i in v.lettres if i in texts],
            )
        )
    rev.rejected = rejected
    s.flush()
    _act_on_opinions(s, config, rev, alerts, mode)
    return rev


def _act_on_opinions(s: Session, config: LoadedConfig, rev: LetterReview, alerts: Any, mode: str) -> None:
    """Explicit recommendations without a proposal get a WATCH one (levels written in the letter
    only); negative opinions on held positions raise a P2 (deduplicated per value and day)."""
    from app.notify.base import Event
    from app.services.decisions import create_proposal, record_signal

    held = {
        r.isin for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
    }
    for op in s.scalars(select(LetterOpinion).where(LetterOpinion.review_id == rev.id)):
        if op.isin is None:
            continue
        if op.explicit and op.action in ("achat", "surveiller") and op.stance != "negative":
            existing = s.scalar(
                select(NewsletterValue.id).where(
                    NewsletterValue.item_id.in_(op.item_ids or [-1]),
                    NewsletterValue.isin == op.isin,
                    NewsletterValue.signal_id.is_not(None),
                )
            )
            if existing is None:
                levels = {}
                nv = s.scalar(
                    select(NewsletterValue).where(
                        NewsletterValue.item_id.in_(op.item_ids or [-1]), NewsletterValue.isin == op.isin
                    )
                )
                if nv is not None and nv.levels:
                    levels = dict(nv.levels)
                item = s.get(NewsletterItem, (op.item_ids or [None])[0]) if op.item_ids else None
                sig = record_signal(
                    s,
                    op.isin,
                    "external",
                    op.source or "lettres",
                    _aware(item.received_at) if item else rev.ts,
                    "eod",
                    "unavailable",
                    config.params.version,
                    evidence={
                        "direction": "buy" if op.action == "achat" else "watch",
                        "via": "revue_lettres",
                        "citation": op.quote,
                        "levels": levels,
                    },
                    entry_low=levels.get("achat"),
                    stop_initial=levels.get("stop"),
                    targets=[levels["objectif"]] if "objectif" in levels else None,
                )
                create_proposal(
                    s,
                    sig,
                    "WATCH",
                    None,
                    {"passed": ["recommandation explicite relevée par la revue des lettres"], "blocked": []},
                    config.params.version,
                    to_verify=True,
                    config=config,
                    entry_zone={"achat": levels["achat"]} if levels.get("achat") else None,
                    stop=levels.get("stop"),
                    targets=[levels["objectif"]] if "objectif" in levels else None,
                )
        if alerts is not None and op.isin in held and (op.stance == "negative" or op.action == "vente"):
            alerts.emit(
                s,
                Event(
                    "P2",
                    "letter_negative_position",
                    f"Avis négatif sur une position : {name_of(op.isin)}",
                    [f"Source : {op.source or '?'}", (op.summary or "")[:200], "Détail : page Revue des lettres"],
                    isin=op.isin,
                ),
                mode,
                False,
            )


def recommendations(s: Session, config: LoadedConfig, day: date | None = None) -> list[dict[str, Any]]:
    """docs/20 §20.5 — deterministic aggregation of the opinions over the window."""
    p = params_of(config)
    window = p.window_days if p else 5
    min_pos = p.min_positive_sources if p else 2
    day = day or datetime.now(PARIS).date()
    start = day - timedelta(days=window)
    ops = [
        o for o in s.scalars(select(LetterOpinion).where(LetterOpinion.day > start, LetterOpinion.day <= day)) if o.isin
    ]
    vals = list(s.scalars(select(LetterValidation).where(LetterValidation.day > start, LetterValidation.day <= day)))
    held = {
        r.isin for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
    }
    watched = {w.isin for w in s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True)))}
    by: dict[str, list[LetterOpinion]] = defaultdict(list)
    for o in ops:
        by[o.isin or ""].append(o)
    out = []
    for isin, lst in by.items():
        pos_src = {o.source or f"lettre {o.item_ids}" for o in lst if o.stance == "positive"}
        neg = [o for o in lst if o.stance == "negative" or o.action == "vente"]
        explicit = [o for o in lst if o.explicit and o.action in ("achat", "surveiller")]
        contra = [v for v in vals if v.isin == isin and v.verdict == "contredit"]
        confirm = [v for v in vals if v.isin == isin and v.verdict == "confirme"]
        kind = None
        if isin in held and neg:
            kind = "vigilance"
        elif contra:
            kind = "contradiction"
        elif len(pos_src) >= min_pos and not neg:
            kind = "a_etudier"
        elif explicit and not neg:
            kind = "explicite"
        if kind is None:
            continue
        last = max(lst, key=lambda o: (o.day, o.id))
        out.append(
            {
                "isin": isin,
                "valeur": name_of(isin),
                "type": kind,
                "sources_positives": sorted(pos_src),
                "avis_negatifs": len(neg),
                "confirmations": len(confirm),
                "contradictions": len(contra),
                "en_position": isin in held,
                "en_watchlist": isin in watched,
                "dernier_resume": last.summary,
            }
        )
    order = {"vigilance": 0, "contradiction": 1, "a_etudier": 2, "explicite": 3}
    return sorted(out, key=lambda r: (order[r["type"]], -len(r["sources_positives"]), r["valeur"]))


REC_LABELS = {
    "vigilance": "Vigilance (position)",
    "contradiction": "Contredit une proposition",
    "a_etudier": "À étudier (convergence)",
    "explicite": "Recommandation explicite",
}


def digest_event(rev: LetterReview, recs: list[dict[str, Any]]) -> Any:
    from app.notify.base import Event

    lines = []
    first = re.split(r"(?<=[.!?])\s", (rev.synthesis or "").strip())
    if first and first[0]:
        lines.append(first[0][:200])
    for r in recs[:8]:
        lines.append(f"{REC_LABELS[r['type']]} : {r['valeur']}")
    lines.append("Détail : page Revue des lettres")
    return Event("P3", "letters_review", f"Revue des lettres du {rev.day:%d/%m}", lines[:11])
