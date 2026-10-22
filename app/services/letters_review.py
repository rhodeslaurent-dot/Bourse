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


def unreviewed_letters(s: Session, now: datetime) -> list[NewsletterItem]:
    """Letters of the last days not yet covered by a successful review (catch-up / manual run)."""
    done: set[int] = set()
    for ids in s.scalars(select(LetterReview.item_ids).where(LetterReview.status == "ok")):
        done.update(ids or [])
    since = now - MAX_LOOKBACK
    rows = s.scalars(select(NewsletterItem).order_by(NewsletterItem.received_at.desc()))
    return [r for r in rows if since < _aware(r.received_at) <= now and r.id not in done]


def cited_isins(s: Session, items: list[NewsletterItem]) -> list[str]:
    """Values cited by the letters, most cited first: extraction at reception (``newsletter_values``)
    plus known names / ISINs found in the text (linked page included)."""
    from collections import Counter

    from app.data.providers.imap_gmail import extract_values
    from app.services.news import alias_index

    counts: Counter[str] = Counter()
    ids = [i.id for i in items]
    for v in s.scalars(select(NewsletterValue).where(NewsletterValue.item_id.in_(ids or [-1]))):
        if v.isin:
            counts[v.isin] += 2
    aliases = alias_index(s)
    for it in items:
        for v in extract_values(it.body_text or "", aliases):
            if v.isin:
                counts[v.isin] += 1
    return [isin for isin, _ in counts.most_common()]


def value_facts(
    s: Session, config: LoadedConfig, isin: str, items: list[NewsletterItem], quote: Any | None, max_news: int
) -> dict[str, Any]:
    """All stored facts on one cited value (docs/20 §20.2): the assistant's fact pack, the quote at
    reception with its status, and the letter's own measures (open, gap, drift)."""
    from app.services.assistant import build_context

    facts = build_context(s, isin, float(config.params.capital.capital_pilote_eur))
    facts.pop("date_du_dossier", None)
    facts.pop("regime_marche", None)  # given once for the whole market
    if isinstance(facts.get("nouvelles_7j"), list):
        facts["nouvelles_7j"] = facts["nouvelles_7j"][:max_news]
    if quote is not None:
        facts["cours_a_la_reception"] = {
            "cours": getattr(quote, "last", None),
            "statut": label("data_status", getattr(getattr(quote, "data_status", None), "value", None)),
            "horodatage_marche": getattr(quote, "market_timestamp", None),
            "source": getattr(quote, "source", None),
        }
    ids = [i.id for i in items]
    measures = [
        {
            "lettre": v.item_id,
            "sens": v.direction,
            "niveaux_ecrits": v.levels,
            "ouverture_du_jour": v.p_open,
            "cloture_veille": v.close_prev,
            "ecart_ouverture": v.gap_open,
            "cours_reception": v.p_recv,
            "statut_cours_reception": v.p_recv_status,
            "derive_depuis_ouverture": v.drift_since_open,
        }
        for v in s.scalars(
            select(NewsletterValue).where(NewsletterValue.item_id.in_(ids or [-1]), NewsletterValue.isin == isin)
        )
    ]
    if measures:
        facts["mesures_a_la_reception"] = measures
    return facts


def market_facts(s: Session) -> dict[str, Any]:
    from app.db.models import PriceEod
    from app.scheduler.jobs.daily_regime import INDEX_ISINS

    reg = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
    out: dict[str, Any] = {}
    if reg:
        out["regime"] = {"jour": reg.day, "regime": label("regime", reg.regime), "details": reg.details}
    for name, isin in INDEX_ISINS.items():
        closes = list(s.scalars(select(PriceEod).where(PriceEod.isin == isin).order_by(PriceEod.day.desc()).limit(5)))
        if closes:
            out[name] = [{"jour": c.day, "cloture": c.close} for c in closes]
    return out


def build_dossier(
    s: Session,
    config: LoadedConfig,
    items: list[NewsletterItem],
    now: datetime,
    quotes: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
                "valeurs": {
                    isin: value_facts(
                        s, config, isin, items, (quotes or {}).get(isin), p.max_news_per_value if p else 5
                    )
                    for isin in cited_isins(s, items)[: (p.max_values if p else 15)]
                },
                "marche": market_facts(s),
                "contexte_macro": _press_context(s, config, now),
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


def _press_context(s: Session, config: LoadedConfig, now: datetime) -> list[dict[str, Any]]:
    """docs/21 §21.6 — macro / sector articles of the press reviews of the last days."""
    from app.services.press_review import macro_context

    lr = getattr(config.params, "letters_review", None)
    extra = (lr.model_extra or {}) if lr is not None and hasattr(lr, "model_extra") else {}
    return macro_context(s, now, int(extra.get("macro_days", 3)), int(extra.get("max_macro_items", 10)))


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
    items: list[NewsletterItem] | None = None,
    quotes: dict[str, Any] | None = None,
) -> LetterReview:
    """Review ``items`` (default: every letter not yet reviewed). At reception (docs/20 §20.1) the
    caller passes the letters just ingested, so signals are analysed while still actionable."""
    from app.llm.gateway import LlmUnavailable
    from app.llm.schemas import LettersReviewOut

    now = now or datetime.now(UTC)
    p = params_of(config)
    items = unreviewed_letters(s, now) if items is None else items
    dossier = build_dossier(s, config, items, now, quotes)
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
            max_tokens=p.max_tokens if p else 10000,
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

    if out.indice is not None:
        rev.index_view = _index_view(out.indice, texts, rev.day, rejected)

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
    if alerts is not None:
        alert_recommendations(s, config, rev, alerts, mode)
    return rev


def _index_view(iv: Any, texts: dict[int, str], day: Any, rejected: list[dict[str, str]]) -> dict[str, Any] | None:
    """Option C of the market gate (docs/07 §7.9.4): levels and advice on the index, kept only if
    the quote is literal and every level is written in a letter (never computed by the LLM)."""
    quote = _norm(iv.citation)
    if not quote or not any(quote in t for t in texts.values()):
        rejected.append({"element": f"indice {iv.indice}", "motif": "citation introuvable dans les lettres"})
        return None
    levels = {k: getattr(iv, k) for k in ("obstacle_bas", "obstacle_haut", "support_bas", "support_haut")}
    shown = " ".join(f"{v:g}" for v in levels.values() if v is not None)
    bad = unsupported_numbers(shown, {"lettres": list(texts.values())}) if shown else []
    if bad:
        rejected.append({"element": f"indice {iv.indice}", "motif": "niveaux absents des lettres : " + ", ".join(bad)})
        return None
    ids = [i for i in iv.lettres if i in texts] or [i for i, t in texts.items() if quote in t]
    return {
        "jour": str(day),
        "indice": iv.indice,
        **levels,
        "conseil": iv.conseil,
        "citation": iv.citation,
        "lettres": ids,
    }


def published_gate(s: Session, config: LoadedConfig, day: Any) -> tuple[bool | None, str]:
    """Option C conditions from the latest index view of the letters (≤ market_gate.max_age_sessions
    sessions old): the reference index closed above the published obstacle zone **and** the letter
    no longer advises waiting. Unknown when there is no recent publication or no stored close."""
    from app.db.models import PriceEod

    gate = getattr(config.params, "market_gate", None) or {}
    max_age = int(gate.get("max_age_sessions", 5))
    rev = s.scalar(
        select(LetterReview)
        .where(LetterReview.index_view.is_not(None), LetterReview.day <= day)
        .order_by(LetterReview.day.desc(), LetterReview.id.desc())
    )
    if rev is None or not rev.index_view:
        return None, "aucune publication des lettres sur l'indice"
    iv = rev.index_view
    rows = list(
        s.scalars(
            select(PriceEod)
            .where(PriceEod.isin == str(gate.get("index_isin", "FR0003500008")), PriceEod.day <= day)
            .order_by(PriceEod.day.desc())
            .limit(max_age + 2)
        )
    )
    if not rows:
        return None, "clôture de l'indice indisponible"
    age = sum(1 for r in rows if r.day > rev.day)
    if age > max_age:
        return None, f"publication sur l'indice du {rev.day:%d/%m} trop ancienne ({age} séances)"
    close = rows[0].close
    top = iv.get("obstacle_haut") or iv.get("obstacle_bas")
    if top is None:
        return None, "aucun obstacle chiffré publié sur l'indice"
    above = close > float(top)
    waits = iv.get("conseil") == "attendre"
    why = (
        f"clôture {close:.2f} {'au-dessus' if above else 'sous'} de l'obstacle {float(top):g} publié le {rev.day:%d/%m}"
        f" ; conseil de la lettre : {iv.get('conseil')}"
    )
    return above and not waits, why


def alert_recommendations(s: Session, config: LoadedConfig, rev: LetterReview, alerts: Any, mode: str) -> int:
    """P2 per value touched by this review that now carries a recommendation (deduplicated per
    value, kind and day by the alert service). Vigilance on positions is emitted separately."""
    from app.notify.base import Event

    touched = {o.isin for o in s.scalars(select(LetterOpinion).where(LetterOpinion.review_id == rev.id)) if o.isin}
    vals = s.scalars(select(LetterValidation).where(LetterValidation.review_id == rev.id))
    touched |= {v.isin for v in vals if v.isin}
    n = 0
    for r in recommendations(s, config, rev.day):
        if r["isin"] not in touched or r["type"] == "vigilance":
            continue
        lines = []
        if r["sources_positives"]:
            lines.append("Avis positifs : " + ", ".join(r["sources_positives"]))
        if r["contradictions"]:
            lines.append(f"Contredit une proposition en cours ({r['contradictions']})")
        if r["dernier_resume"]:
            lines.append(r["dernier_resume"][:200])
        lines.append("Pas un ordre : vérifier avec les règles de risque · page Revue des lettres")
        alerts.emit(
            s,
            Event("P2", f"letter_{r['type']}", f"{REC_LABELS[r['type']]} : {r['valeur']}", lines, isin=r["isin"]),
            mode,
            True,
        )
        n += 1
    return n


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
    first = re.split(r"(?<=[.!?])\s", (rev.synthesis or "").strip()) if rev.status == "ok" else []
    if first and first[0]:
        lines.append(first[0][:200])
    for r in recs[:8]:
        lines.append(f"{REC_LABELS[r['type']]} : {r['valeur']}")
    lines.append("Détail : page Revue des lettres")
    return Event("P3", "letters_review", f"Revue des lettres du {rev.day:%d/%m}", lines[:11])
