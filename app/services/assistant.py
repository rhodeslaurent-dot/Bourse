"""Grounded assistant (docs/17 §17.4): Python builds a fact pack, the LLM only phrases an answer
from it; any figure absent from the pack rejects the answer (same rule as M16)."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    AssistantMessage,
    Decision,
    DecisionSnapshot,
    Instrument,
    LearningQuestion,
    LearningSuggestion,
    MarketRegimeRow,
    NewsItem,
    Order,
    PortfolioState,
    PositionRow,
    PriceEod,
    Proposal,
    Signal,
    TraderMemory,
    WatchlistRow,
)
from app.services.explain import explain_proposal
from app.services.market_data import latest_features
from app.services.notes import get_note
from app.web.labels import FEATURE_LABELS, label

ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\w])")


def resolve_isin(s: Session, text: str) -> str | None:
    """ISIN typed or found in the text, else a unique instrument whose name matches."""
    if not text:
        return None
    m = ISIN_RE.search(text.upper())
    if m:
        return m.group(1)
    q = text.strip()
    if len(q) < 3:
        return None
    rows = list(s.scalars(select(Instrument).where(func.lower(Instrument.name).like(f"%{q.lower()}%")).limit(2)))
    return rows[0].isin if len(rows) == 1 else None


def search_instruments(s: Session, q: str, limit: int = 12) -> list[Instrument]:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    return list(
        s.scalars(
            select(Instrument)
            .where(
                or_(
                    Instrument.isin == q.upper(),
                    func.lower(Instrument.name).like(f"%{q.lower()}%"),
                    Instrument.ticker_local == q.upper(),
                )
            )
            .order_by(Instrument.in_universe.desc(), Instrument.name)
            .limit(limit)
        )
    )


def _d(x: Any) -> Any:
    if isinstance(x, datetime):
        return x.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if x.tzinfo else x.strftime("%Y-%m-%d %H:%M")
    if isinstance(x, date):
        return x.isoformat()
    if isinstance(x, float):
        return round(x, 4)
    return x


def build_context(
    s: Session, isin: str | None, capital: float | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Fact pack: only stored data, with labels, dates and statuses. No computation beyond what
    the tool already stores (indicators, explanations)."""
    now = now or datetime.now(UTC)
    ctx: dict[str, Any] = {"date_du_dossier": _d(now)}
    reg = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
    if reg:
        ctx["regime_marche"] = {"jour": _d(reg.day), "regime": label("regime", reg.regime)}
    if isin is None:
        ctx["positions_ouvertes"] = [
            {
                "isin": r.isin,
                "quantite": r.qty_held,
                "prix_de_revient": _d(r.avg_price),
                "stop": r.stop_current,
                "protection": label("protection_state", r.protection_state),
            }
            for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
        ]
        ctx["watchlist"] = [
            {"isin": w.isin, "force_relative": _d(w.rs_rank), "motif": label("watch_reason", w.reason)}
            for w in s.scalars(
                select(WatchlistRow)
                .where(WatchlistRow.active.is_(True))
                .order_by(WatchlistRow.rs_rank.desc().nulls_last())
                .limit(15)
            )
        ]
        note = get_note(s, "global", "todo")
        if note and note.text:
            ctx["note_utilisateur_a_faire"] = note.text
        return ctx
    inst = s.get(Instrument, isin)
    ctx["valeur"] = (
        {
            "isin": isin,
            "nom": inst.name,
            "place": inst.mic,
            "devise": inst.currency,
            "secteur": inst.sector,
            "capitalisation_eur": _d(inst.market_cap_eur),
            "volume_moyen_20j_eur": _d(inst.adv_eur_20),
            "eligible_pea": {True: "oui (réglementaire)", False: "non", None: "inconnu"}[inst.pea_eligible],
            "srd": f"{inst.srd_status} (à confirmer chez Saxo)",
            "dans_univers": inst.in_universe,
            "motifs_exclusion": inst.universe_reasons or [],
            "prochains_resultats": _d(inst.earnings_next_date),
        }
        if inst
        else {"isin": isin, "nom": None, "note": "valeur absente du référentiel"}
    )
    closes = list(s.scalars(select(PriceEod).where(PriceEod.isin == isin).order_by(PriceEod.day.desc()).limit(5)))
    ctx["dernieres_clotures"] = [
        {"jour": _d(c.day), "cloture": _d(c.close), "volume": c.volume, "statut": label("data_status", c.data_status)}
        for c in closes
    ]
    f = latest_features(s, [isin]).get(isin)
    if f:
        ctx["indicateurs"] = {
            "jour": _d(f.day),
            **{FEATURE_LABELS[k]: _d(getattr(f, k)) for k in FEATURE_LABELS if getattr(f, k, None) is not None},
            "trou_de_donnees": f.gap_in_data,
        }
    pos = [
        r
        for r in s.scalars(
            select(PositionRow).where(
                PositionRow.isin == isin, PositionRow.closed_at.is_(None), PositionRow.qty_held > 0
            )
        )
    ]
    ctx["position"] = [
        {
            "quantite": r.qty_held,
            "prix_de_revient": _d(r.avg_price),
            "execution": label("execution_state", r.execution_state),
            "protection": label("protection_state", r.protection_state),
            "quantite_protegee": r.qty_protected,
            "stop_courant": r.stop_current,
            "dernier_cours": _d(r.last_price),
            "statut_cours": label("data_status", r.last_price_status) if r.last_price_status else None,
            "note_utilisateur": (
                get_note(s, "position", str(r.id)).text if get_note(s, "position", str(r.id)) else None
            ),
        }
        for r in pos
    ]
    since = now - timedelta(days=30)
    props = [
        p
        for p in s.scalars(
            select(Proposal)
            .where(Proposal.isin == isin, Proposal.deleted_at.is_(None))
            .order_by(Proposal.id.desc())
            .limit(5)
        )
        if (p.created_at.replace(tzinfo=UTC) if p.created_at.tzinfo is None else p.created_at) >= since
    ]
    ctx["propositions_30j"] = [
        {
            "date": _d(p.created_at),
            "explication": explain_proposal(p, s.get(Signal, p.signal_id), None, capital).summary,
            "decisions": [
                label("decision", d.decision) for d in s.scalars(select(Decision).where(Decision.proposal_id == p.id))
            ],
        }
        for p in props
    ]
    news = [
        n
        for n in s.scalars(
            select(NewsItem)
            .where(NewsItem.fetched_at >= now - timedelta(days=7))
            .order_by(NewsItem.id.desc())
            .limit(300)
        )
        if isin in (n.isins or [])
    ]
    ctx["nouvelles_7j"] = [
        {
            "date": _d(n.published_at or n.fetched_at),
            "source": n.source,
            "titre": n.title[:200],
            "classement": (n.classification or {}).get("type"),
            "sens": (n.classification or {}).get("direction"),
        }
        for n in news[:8]
    ]
    for scope in ("isin", "watchlist"):
        note = get_note(s, scope, isin)
        if note and note.text:
            ctx[f"note_utilisateur_{scope}"] = note.text
    from app.db.models import LetterOpinion

    ops = [
        o
        for o in s.scalars(
            select(LetterOpinion).where(LetterOpinion.isin == isin).order_by(LetterOpinion.id.desc()).limit(10)
        )
        if o.day >= (now - timedelta(days=7)).date()
    ]
    if ops:
        ctx["avis_lettres_7j"] = [
            {
                "jour": _d(o.day),
                "source": o.source,
                "orientation": o.stance,
                "recommandation": o.action if o.explicit else None,
                "resume": o.summary,
            }
            for o in ops
        ]
    w = s.scalar(select(WatchlistRow).where(WatchlistRow.isin == isin, WatchlistRow.active.is_(True)))
    if w:
        ctx["watchlist"] = {
            "motif": label("watch_reason", w.reason),
            "force_relative": _d(w.rs_rank),
            "niveaux_notes": w.levels,
        }
    return ctx


PAGES = {
    "/": "Décisions du jour",
    "/opportunites": "Opportunités",
    "/positions": "Positions",
    "/watchlist": "Watchlist",
    "/journee": "Journée",
    "/performance": "Performance",
    "/apprentissage": "Apprentissage",
    "/lettres": "Revue des lettres",
    "/sante": "Santé",
    "/assistant": "Assistant",
    "/import": "Import",
}


def page_key(path: str | None) -> str | None:
    """Normalise a request path to a known page key ('/valeur/<isin>' → '/valeur')."""
    if not path:
        return None
    if path.startswith("/valeur/"):
        return "/valeur"
    return path if path in PAGES else None


def page_facts(s: Session, page: str | None, config: Any | None, now: datetime) -> dict[str, Any] | None:
    """Facts of the page the question was asked from (docs/18 §18.3). Stored or Python-computed
    values only; never job error texts."""
    if page is None or page in ("/valeur", "/assistant"):
        return None
    out: dict[str, Any] = {"nom": PAGES.get(page, page)}
    if page == "/" and config is not None:
        from app.services.risk_view import build_risk_view

        view = build_risk_view(s, config)
        r = view["risk"]
        out["risque"] = {
            "risque_ouvert_pct_du_capital": _d(getattr(r, "open_risk_pct", None)),
            "plafond_pct": view["max_open_risk_pct"],
            "risque_courant_eur": _d(getattr(r, "open_risk_current_eur", None)),
            "risque_reserve_eur": _d(getattr(r, "open_risk_reserved_eur", None)),
            "alertes": list(getattr(r, "alerts", []) or []),
            "incomplet": view["incomplete"],
        }
        out["positions_non_protegees"] = [
            {"isin": p.isin, "quantite_sans_stop": p.qty - p.qty_protected} for p in view["unprotected"]
        ]
    elif page == "/opportunites":
        day = now.date()
        rows = []
        for p in s.scalars(
            select(Proposal).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id.desc()).limit(80)
        ):
            created = p.created_at if p.created_at.tzinfo else p.created_at.replace(tzinfo=UTC)
            if created.date() != day:
                continue
            snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == p.id))
            ex = explain_proposal(p, s.get(Signal, p.signal_id), snap, None)
            rows.append(
                {
                    "isin": p.isin,
                    "action": label("action", p.action),
                    "explication": ex.summary,
                    "historique_comparable": ex.track,
                    "a_verifier": p.to_verify,
                }
            )
        out["propositions_du_jour"] = rows[:20]
    elif page == "/positions":
        out["liquidites"] = [
            {
                "compte_id": st.account_id,
                "cash": _d(st.cash),
                "declaratif": st.declarative,
                "synchronise": _d(st.synced_at),
            }
            for st in s.scalars(select(PortfolioState))
        ]
        out["ordres_en_attente"] = [
            {"isin": o.isin, "sens": o.side, "quantite": o.qty, "etat": label("order_state", o.state)}
            for o in s.scalars(select(Order).where(Order.state.in_(["entered", "partially_filled"])))
        ]
    elif page == "/journee" and config is not None:
        from app.services.workflow import day_view

        steps = day_view(s, config.params, now.date(), True, "disponible")
        out["etapes"] = [
            {"etape": st.label, "creneau": st.slot, "faite": st.done_at is not None, "applicable": st.applicable}
            for st in steps
        ]
    elif page == "/performance" and config is not None:
        from app.services.performance import build_performance

        data = build_performance(s, config)
        v, q = data["v"], data["q"]
        out["synthese"] = {
            "realise_eur": _d(v.realized_total),
            "latent_eur": _d(v.latent_total),
            "frais_eur": _d(v.fees_total),
            "trades_clos": q.n,
            "taux_de_reussite": _d(q.win_rate),
            "esperance_R": _d(q.expectancy_r),
            "profit_factor": _d(q.profit_factor),
            "fiabilite": q.reliability,
            "donnees_incompletes": v.incomplete,
        }
    elif page == "/apprentissage" and config is not None:
        from app.services.learning import aggregates
        from app.services.outcomes import learning_params

        if learning_params(config) is not None:
            agg = aggregates(s, config)
            out["retour_experience"] = {k: agg[k] for k in ("echantillon", "global", "par_decision", "par_source")}
            out["questions_ouvertes"] = [
                q.question for q in s.scalars(select(LearningQuestion).where(LearningQuestion.status == "open"))
            ]
            out["propositions_en_attente"] = [
                x.title
                for x in s.scalars(select(LearningSuggestion).where(LearningSuggestion.status.in_(["open", "later"])))
            ]
    elif page == "/lettres" and config is not None:
        from app.db.models import LetterReview
        from app.services.letters_review import recommendations

        rev = s.scalar(select(LetterReview).order_by(LetterReview.ts.desc()))
        if rev is not None:
            out["synthese_du_jour"] = rev.synthesis
        out["preconisations"] = recommendations(s, config)
    elif page == "/sante":
        from app.db.repo import last_runs

        out["jobs"] = [
            {"job": name, "statut": r.status, "fin": _d(r.ended_at), "lignes": r.rows}
            for name, r in sorted(last_runs(s).items())
        ]
    return out


def continuity(s: Session, page: str | None, isin: str | None, now: datetime, limit: int = 2) -> list[dict[str, Any]]:
    """Last exchanges of the same context (< 2 h) — already validated answers."""
    since = now - timedelta(hours=2)
    rows = []
    for m in s.scalars(select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(20)):
        ts = m.ts if m.ts.tzinfo else m.ts.replace(tzinfo=UTC)
        if ts < since or m.page != page or m.isin != isin or m.answered_by != "llm":
            continue
        rows.append({"question": m.question, "reponse": (m.answer or {}).get("answer", "")})
        if len(rows) >= limit:
            break
    return list(reversed(rows))


def _num(token: str) -> float | None:
    t = re.sub(r"[   ]", "", token).replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def unsupported_numbers(answer: str, context: dict[str, Any], question: str = "") -> list[str]:
    """Numbers of the answer that do not appear in the fact pack (or the question), accepting the
    precision the answer uses (62 matches 62.3) and percentages of stored fractions (3,4 % ↔ 0.034)."""
    pool_text = json.dumps(context, ensure_ascii=False, default=str) + " " + question
    pool = [v for v in (_num(m) for m in NUM_RE.findall(pool_text)) if v is not None]
    bad: list[str] = []
    for tok in NUM_RE.findall(answer):
        v = _num(tok)
        if v is None:
            continue
        decimals = len(re.split(r"[.,]", tok)[1]) if re.search(r"[.,]\d+$", tok) else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if v <= 10 and decimals == 0:
            continue  # small counts ("3 nouvelles", "2 propositions") are not market figures
        # a percentage matches a stored *fraction* only (0.034 ↔ 3,4 %), never an integer (9 ↛ 900)
        if any(abs(p - v) <= tol or (abs(p) < 2 and abs(p * 100 - v) <= tol) for p in pool):
            continue
        bad.append(tok)
    return bad


def ask(
    s: Session,
    gateway: Any,
    question: str,
    isin: str | None,
    capital: float | None,
    max_tokens: int = 800,
    page: str | None = None,
    config: Any | None = None,
) -> AssistantMessage:
    """Answer with the LLM when available; otherwise store the fact pack (rules fallback).
    ``page`` adds the facts of the page the question comes from (docs/18 §18.3)."""
    from app.llm.gateway import LlmUnavailable
    from app.llm.schemas import AssistantAnswer

    now = datetime.now(UTC)
    page = page_key(page)
    ctx = build_context(s, isin, capital)
    facts = page_facts(s, page, config, now)
    if facts:
        ctx["page"] = facts
    prev = continuity(s, page, isin, now)
    if prev:
        ctx["echanges_precedents"] = prev
    lp = getattr(getattr(config, "params", None), "learning", None)
    lessons = s.scalars(
        select(TraderMemory)
        .where(TraderMemory.active.is_(True))
        .order_by(TraderMemory.id.desc())
        .limit(lp.memory_max_items if lp else 30)
    )
    lessons_txt = [m.text for m in lessons]
    if lessons_txt:
        ctx["lecons_retenues"] = lessons_txt
    msg = AssistantMessage(
        ts=now,
        isin=isin,
        page=page,
        question=question.strip()[:2000],
        context=json.loads(json.dumps(ctx, default=str)),
        answered_by="rules",
    )
    s.add(msg)
    if gateway is not None and getattr(gateway, "enabled", False):
        user = f"<dossier>\n{json.dumps(ctx, ensure_ascii=False, default=str, indent=1)}\n</dossier>\n\nQuestion : {question.strip()}"  # noqa: E501
        try:
            parsed, usage = gateway.parse(
                s,
                "assistant",
                "assistant",
                user,
                AssistantAnswer,
                model=gateway.settings.write_model,
                max_tokens=max_tokens,
            )
            msg.cost_usd, msg.model = usage.cost_usd, gateway.settings.write_model
            bad = unsupported_numbers(parsed.answer, ctx, question)
            if bad:
                msg.answered_by = "rejected"
                msg.answer = {
                    "answer": "Réponse rejetée : chiffre non présent dans les données ("
                    + ", ".join(bad[:5])
                    + "). Voir le dossier de faits ci-dessous.",
                    "facts_used": [],
                    "caveats": [],
                    "not_available": [],
                }
            else:
                msg.answered_by = "llm"
                msg.answer = parsed.model_dump()
        except LlmUnavailable as exc:
            msg.answer = {
                "answer": f"Assistant indisponible ({exc}) : voici les données de l'outil.",
                "facts_used": [],
                "caveats": [],
                "not_available": [],
            }
    else:
        msg.answer = {
            "answer": "Assistant IA non configuré (clé Anthropic absente) : voici les données de l'outil sur lesquelles une réponse s'appuierait.",  # noqa: E501
            "facts_used": [],
            "caveats": [],
            "not_available": [],
        }
    s.flush()
    return msg
