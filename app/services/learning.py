"""Learning loop (docs/18 §18.5–18.6): dossier of computed aggregates, AI review with Python guards,
questions / suggestions handling and the trader memory.

The LLM never computes: it reads aggregates built here and every number it writes must exist in
the dossier; a parameter suggestion is checked against ``params.yaml`` and never applied.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import (
    LearningQuestion,
    LearningReview,
    LearningSuggestion,
    Proposal,
    Signal,
    SignalOutcome,
    TraderMemory,
)
from app.domain.learning import GroupStat, Sample, group_stats, stat_of, threshold_table
from app.services.assistant import unsupported_numbers
from app.services.instruments import name_of
from app.services.notes import notes_for
from app.services.outcomes import learning_params, samples
from app.web.labels import label

GROUPINGS = {
    "par_detecteur": ("detector", "detector"),
    "par_source": ("source", None),
    "par_action": ("action", "action"),
    "par_regime": ("regime", "regime"),
    "par_decision": ("decision_group", "decision_group"),
    "par_heure": ("hour", None),
}

# Candidate thresholds: a parameter suggestion may only propose one of these bucket edges.
CANDIDATES: list[dict[str, Any]] = [
    {
        "params_key": "detectors.d5_external.drift_since_open_max_for_buy",
        "fait": "drift",
        "libelle": "dérive depuis l'ouverture à la réception (signaux externes)",
        "bornes": [0.0, 0.01, 0.02, 0.03, 0.05],
        "fmt": lambda v: f"{v:.0%}",
    },
    {
        "params_key": "universe.momentum_watchlist_rs_rank_min",
        "fait": "rs_rank",
        "libelle": "force relative (rang 0–100) au moment de la proposition",
        "bornes": [60.0, 70.0, 80.0, 90.0],
        "fmt": lambda v: f"{v:.0f}",
    },
]

PARAM_KEYS_SHOWN = [
    "detectors.d5_external.drift_since_open_max_for_buy",
    "universe.momentum_watchlist_rs_rank_min",
    "universe.momentum_watchlist_max",
    "review.profitability_min_signals_per_detector",
    "learning.horizon_sessions",
    "learning.entry_validity_sessions",
    "learning.favorable_r",
    "learning.unfavorable_r",
    "risk.max_risk_per_trade_pct",
    "risk.max_open_risk_pct",
]


def norm_text(text: str | None) -> str:
    """Comparison key for de-duplication: case, accents, punctuation and spacing ignored."""
    import unicodedata

    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


TOOL_CONTEXT = {
    "decisions": "chaque décision (Vu, Watch, Ordre saisi, Ignorer) est enregistrée automatiquement depuis "
    "l'interface ou Telegram, avec un motif facultatif : c'est déjà le journal de décision",
    "resultats": "le résultat de chaque proposition, prise ou non, est calculé automatiquement chaque matin de bourse "
    "à 07:20 ; il devient définitif quand le stop ou l'objectif est touché, quand l'entrée n'a pas été atteinte "
    "pendant sa validité, ou à la fin de l'horizon",
    "revue": "une analyse automatique a lieu chaque samedi à 08:00, et à la demande",
    "notes": "une note libre peut être saisie sur chaque proposition, position et valeur ; elle est reprise ici",
    "valeurs_vides": "une statistique vide (null) signifie qu'aucun résultat n'est encore définitif, "
    "pas un défaut de configuration du suivi",
}


def param_value(config: LoadedConfig, dotted: str) -> Any:
    cur: Any = config.raw
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _r2(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def stat_row(g: GroupStat, key_label: Callable[[str], str] | None = None) -> dict[str, Any]:
    return {
        "groupe": key_label(g.key) if key_label else g.key,
        "cas_avec_R": g.n,
        "reussite_pct": None if g.win_rate is None else round(g.win_rate * 100),
        "esperance_R": _r2(g.mean_r),
        "ic90_R": None if g.ci_low is None else [_r2(g.ci_low), _r2(g.ci_high)],
        "mediane_R": _r2(g.median_r),
        "cas_avec_rendement": g.n_ret,
        "rendement_moyen_pct": None if g.mean_ret_pct is None else round(g.mean_ret_pct * 100, 1),
        "fiabilite": g.reliability,
    }


def aggregates(s: Session, config: LoadedConfig) -> dict[str, Any]:
    """Everything the page shows and the review reads, computed in Python."""
    lp = learning_params(config)
    min_disp = lp.min_samples_display if lp else 20
    min_judge = int((config.params.review or {}).get("profitability_min_signals_per_detector", 60))
    ss = samples(s)
    statuses: dict[str, int] = {}
    for st in s.scalars(select(SignalOutcome.status)):
        statuses[st] = statuses.get(st, 0) + 1
    out: dict[str, Any] = {
        "echantillon": {
            "propositions_evaluees": sum(statuses.values()),
            "resultats_definitifs": len(ss),
            "par_statut": {label("outcome_status", k): v for k, v in sorted(statuses.items())},
            "seuil_indicatif": min_disp,
            "seuil_suffisant": min_judge,
        },
        "global": stat_row(stat_of("toutes", ss, min_disp, min_judge)),
    }
    labelers: dict[str, Callable[[str], str]] = {
        "detector": lambda k: label("detector", k),
        "action": lambda k: label("action", k),
        "regime": lambda k: label("regime", k),
        "decision_group": lambda k: label("decision_group", k),
    }
    for name, (key, lab) in GROUPINGS.items():
        out[name] = [stat_row(g, labelers.get(lab) if lab else None) for g in group_stats(ss, key, min_disp, min_judge)]
    out["seuils_candidats"] = []
    for c in CANDIDATES:
        rows = threshold_table(ss, c["fait"], c["bornes"], min_disp, min_judge, fmt=c["fmt"])
        if rows:
            out["seuils_candidats"].append(
                {
                    "parametre": c["params_key"],
                    "libelle": c["libelle"],
                    "valeur_actuelle": param_value(config, c["params_key"]),
                    "bornes": c["bornes"],
                    "tranches": [stat_row(g) for g in rows],
                }
            )
    out["gagnants_manques"], out["pertes_evitees"] = _notable(s, ss)
    return out


def _notable(s: Session, ss: list[Sample]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def row(x: Sample) -> dict[str, Any]:
        p = s.get(Proposal, x.proposal_id)
        return {
            "valeur": name_of(p.isin) if p else "?",
            "date": x.created_at.date().isoformat(),
            "source": x.keys.get("source"),
            "decision": label("decision_group", x.keys.get("decision_group") or ""),
            "R": _r2(x.r),
        }

    not_taken = [x for x in ss if x.keys.get("decision_group") != "prise" and x.r is not None]
    missed = sorted((x for x in not_taken if x.r is not None and x.r >= 2), key=lambda x: -(x.r or 0))[:10]
    avoided = sorted((x for x in not_taken if x.r is not None and x.r < 0), key=lambda x: x.r or 0)[:10]
    return [row(x) for x in missed], [row(x) for x in avoided]


def memory_items(s: Session, limit: int = 30) -> list[TraderMemory]:
    return list(
        s.scalars(
            select(TraderMemory).where(TraderMemory.active.is_(True)).order_by(TraderMemory.id.desc()).limit(limit)
        )
    )


def build_dossier(s: Session, config: LoadedConfig) -> dict[str, Any]:
    lp = learning_params(config)
    dossier = aggregates(s, config)
    outcome_ids = [o.proposal_id for o in s.scalars(select(SignalOutcome).where(SignalOutcome.final.is_(True)))]
    notes = notes_for(s, "proposal", [str(i) for i in outcome_ids[-200:]])
    dossier["notes_propositions"] = [
        {"proposition": k, "note": v.text[:300]} for k, v in list(notes.items())[-15:] if v.text
    ]
    dossier["questions_precedentes"] = [
        {"question": q.question, "reponse": q.answer}
        for q in s.scalars(
            select(LearningQuestion)
            .where(LearningQuestion.status == "answered")
            .order_by(LearningQuestion.id.desc())
            .limit(10)
        )
    ]
    dossier["propositions_precedentes"] = [
        {"titre": x.title, "statut": x.status, "commentaire": x.user_comment}
        for x in s.scalars(
            select(LearningSuggestion)
            .where(LearningSuggestion.status != "open")
            .order_by(LearningSuggestion.id.desc())
            .limit(10)
        )
    ]
    dossier["propositions_en_attente"] = [
        {"titre": x.title, "statut": x.status}
        for x in s.scalars(select(LearningSuggestion).where(LearningSuggestion.status.in_(["open", "later"])))
    ]
    dossier["questions_en_attente"] = [
        q.question for q in s.scalars(select(LearningQuestion).where(LearningQuestion.status == "open"))
    ]
    statuses = list(s.scalars(select(SignalOutcome.status)))
    first = s.scalar(select(Proposal.created_at).where(Proposal.deleted_at.is_(None)).order_by(Proposal.id))
    dossier["resultats_en_cours"] = {
        "en_cours_ou_en_attente": sum(1 for x in statuses if x in ("pending", "running")),
        "plus_ancienne_proposition_suivie": first.date().isoformat() if first else None,
    }
    dossier["contexte_outil"] = TOOL_CONTEXT
    dossier["lecons_retenues"] = [m.text for m in memory_items(s, lp.memory_max_items if lp else 30)]
    dossier["parametres_courants"] = {k: param_value(config, k) for k in PARAM_KEYS_SHOWN}
    return json.loads(json.dumps(dossier, default=str))


def _rules_summary(d: dict[str, Any]) -> str:
    e, g = d["echantillon"], d["global"]
    if not e["resultats_definitifs"]:
        return (
            f"{e['propositions_evaluees']} propositions suivies, aucun résultat définitif pour l'instant : "
            "les statistiques apparaîtront au fil des séances (horizon de 20 séances)."
        )
    return (
        f"{e['resultats_definitifs']} résultats définitifs sur {e['propositions_evaluees']} propositions suivies ; "
        f"espérance globale {g['esperance_R']} R sur {g['cas_avec_R']} cas avec stop (fiabilité : {g['fiabilite']}). "
        "Analyse par l'IA indisponible : statistiques calculées seules."
    )


def _guard_text(texts: list[str], dossier: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    for t in texts:
        bad += unsupported_numbers(t, dossier)
    return bad


def check_suggestion(sug: Any, dossier: dict[str, Any], config: LoadedConfig) -> tuple[str | None, Any]:
    """Return (reason to reject or None, current value read by Python)."""
    if sug.kind != "parametre":
        return None, None
    if not sug.params_key:
        return "paramètre non précisé", None
    current = param_value(config, sug.params_key)
    if current is None or isinstance(current, dict | list):
        return f"clé inconnue ou non numérique dans params.yaml : {sug.params_key}", None
    if sug.proposed_value is None:
        return "valeur proposée absente", current
    if sug.params_key.startswith("risk.") and isinstance(current, int | float) and sug.proposed_value > current:
        return "hausse d'un plafond de risque refusée (CLAUDE.md règle 7)", current
    cand = next((c for c in CANDIDATES if c["params_key"] == sug.params_key), None)
    if cand is not None and float(sug.proposed_value) not in [float(b) for b in cand["bornes"]]:
        return "valeur proposée hors des bornes calculées", current
    if cand is None and unsupported_numbers(str(sug.proposed_value), dossier):
        return "valeur proposée absente du dossier", current
    return None, current


def run_review(s: Session, config: LoadedConfig, gateway: Any, trigger: str = "manual") -> LearningReview:
    from app.llm.gateway import LlmUnavailable
    from app.llm.schemas import LearningReviewOut

    dossier = build_dossier(s, config)
    now = datetime.now(UTC)
    rev = LearningReview(ts=now, trigger=trigger, status="rules", dossier=dossier, rejected=[])
    s.add(rev)
    s.flush()
    if gateway is None or not getattr(gateway, "enabled", False):
        rev.summary = _rules_summary(dossier)
        return rev
    user = f"<dossier>\n{json.dumps(dossier, ensure_ascii=False, indent=1)}\n</dossier>"
    max_tokens = config.params.llm.review_max_tokens if config.params.llm else 4000
    try:
        out, usage = gateway.parse(
            s,
            "learning_review",
            "learning_review",
            user,
            LearningReviewOut,
            model=gateway.settings.write_model,
            max_tokens=max_tokens,
        )
    except LlmUnavailable as exc:
        rev.status, rev.summary = "unavailable", _rules_summary(dossier) + f" ({exc})"
        return rev
    rev.cost_usd, rev.model, rev.status = usage.cost_usd, gateway.settings.write_model, "ok"
    rejected: list[dict[str, Any]] = []
    bad = _guard_text([out.summary], dossier)
    rev.summary = (
        out.summary if not bad else _rules_summary(dossier) + " (synthèse IA écartée : chiffre absent des données)"
    )
    if bad:
        rejected.append({"element": "synthèse", "motif": "chiffres absents du dossier : " + ", ".join(bad[:5])})
    findings = []
    for f in out.findings:
        b = _guard_text([f.title, f.detail], dossier)
        if b:
            rejected.append({"element": f.title, "motif": "chiffres absents du dossier : " + ", ".join(b[:5])})
        else:
            findings.append(f.model_dump())
    rev.findings = findings
    asked = {
        norm_text(q.question)
        for q in s.scalars(select(LearningQuestion).where(LearningQuestion.status.in_(["open", "answered"])))
    }
    for q in out.questions:
        b = _guard_text([q.question, q.why, *q.choices], dossier)
        if b:
            rejected.append({"element": q.question, "motif": "chiffres absents : " + ", ".join(b[:5])})
            continue
        if norm_text(q.question) in asked:
            rejected.append({"element": q.question, "motif": "doublon : question déjà posée"})
            continue
        asked.add(norm_text(q.question))
        s.add(
            LearningQuestion(
                review_id=rev.id,
                ts=now,
                question=q.question,
                why=q.why,
                kind=q.kind,
                choices=q.choices or None,
                status="open",
            )
        )
    known: dict[str, str] = {}
    for x in s.scalars(select(LearningSuggestion).where(LearningSuggestion.status != "duplicate")):
        known[norm_text(x.title)] = x.status
        if x.params_key and x.proposed_value is not None:
            known[f"{x.params_key}={x.proposed_value}"] = x.status
    for sug in out.suggestions:
        b = _guard_text([sug.title, sug.rationale, sug.expected_effect], dossier)
        why, current = check_suggestion(sug, dossier, config)
        if b or why:
            rejected.append({"element": sug.title, "motif": why or "chiffres absents : " + ", ".join(b[:5])})
            continue
        keys = [norm_text(sug.title)]
        if sug.kind == "parametre" and sug.params_key and sug.proposed_value is not None:
            keys.append(f"{sug.params_key}={sug.proposed_value}")
        seen = next((known[k] for k in keys if k in known), None)
        if seen is not None:
            label_status = {"open": "en attente", "later": "reportée", "accepted": "acceptée", "rejected": "refusée"}
            rejected.append(
                {"element": sug.title, "motif": f"doublon : déjà proposée ({label_status.get(seen, seen)})"}
            )
            continue
        for k in keys:
            known[k] = "open"
        s.add(
            LearningSuggestion(
                review_id=rev.id,
                ts=now,
                kind=sug.kind,
                title=sug.title,
                rationale=sug.rationale,
                expected_effect=sug.expected_effect,
                params_key=sug.params_key if sug.kind == "parametre" else None,
                current_value=current,
                proposed_value=sug.proposed_value if sug.kind == "parametre" else None,
                status="open",
            )
        )
    rev.rejected = rejected
    s.flush()
    return rev


def add_memory(s: Session, text: str, source: str = "utilisateur", ref: str | None = None) -> TraderMemory | None:
    text = text.strip()
    if not text:
        return None
    m = TraderMemory(ts=datetime.now(UTC), text=text[:1000], source=source, ref=ref, active=True)
    s.add(m)
    s.flush()
    return m


def answer_question(s: Session, qid: int, answer: str) -> LearningQuestion:
    q = s.get(LearningQuestion, qid)
    if q is None:
        raise ValueError("question inconnue")
    answer = answer.strip()
    if not answer:
        q.status = "dismissed"
        return q
    q.answer, q.answered_at, q.status = answer[:2000], datetime.now(UTC), "answered"
    add_memory(s, f"Question : {q.question} — Réponse : {answer}", "reponse", f"question:{q.id}")
    return q


def effective_from() -> str:
    return (datetime.now(UTC) + timedelta(days=1)).date().isoformat()


def yaml_snippet(sug: LearningSuggestion) -> str | None:
    if sug.kind != "parametre" or not sug.params_key:
        return None
    parts = sug.params_key.split(".")
    lines = [f"# {sug.title} — accepté le {datetime.now(UTC):%d/%m/%Y}, effet au {effective_from()}"]
    for depth, p in enumerate(parts):
        val = f" {sug.proposed_value:g}" if depth == len(parts) - 1 and sug.proposed_value is not None else ""
        lines.append("  " * depth + f"{p}:{val}" + (f"   # était {sug.current_value}" if val else ""))
    return "\n".join(lines)


def adr_draft(sug: LearningSuggestion) -> str:
    return "\n".join(
        [
            f"# ADR — {sug.title}",
            "",
            "Statut : accepté par l'utilisateur sur /apprentissage (à reporter dans params.yaml).",
            f"Date d'effet : {effective_from()}",
            "",
            "## Contexte",
            sug.rationale or "",
            "",
            "## Décision",
            (
                f"`{sug.params_key}` : {sug.current_value} → {sug.proposed_value:g}"
                if sug.params_key and sug.proposed_value is not None
                else sug.title
            ),
            "",
            "## Effet attendu",
            sug.expected_effect or "",
            "",
            "## Réversibilité",
            "Revenir à la valeur précédente dans params.yaml ; les propositions historisées gardent leur version.",
        ]
    )


def decide_suggestion(s: Session, sid: int, status: str, comment: str = "") -> LearningSuggestion:
    if status not in ("accepted", "rejected", "later"):
        raise ValueError("statut invalide")
    sug = s.get(LearningSuggestion, sid)
    if sug is None:
        raise ValueError("proposition inconnue")
    sug.status, sug.user_comment, sug.decided_at = status, (comment.strip() or None), datetime.now(UTC)
    if status == "accepted":
        if sug.kind == "parametre":
            text = (
                f"Décidé : {sug.params_key} {sug.current_value} → {sug.proposed_value:g} "
                f"(à reporter dans params.yaml, effet au {effective_from()}) — {sug.title}"
            )
        else:
            text = f"{sug.title} — {(sug.rationale or '')[:300]}"
        add_memory(
            s,
            text + (f" (commentaire : {comment.strip()})" if comment.strip() else ""),
            "suggestion",
            f"suggestion:{sug.id}",
        )
    return sug


def outcome_rows(s: Session, limit: int = 100) -> list[dict[str, Any]]:
    """Past proposals with their outcome for the page (most recent first)."""
    rows = []
    for o in s.scalars(select(SignalOutcome).order_by(SignalOutcome.proposal_id.desc()).limit(limit)):
        p = s.get(Proposal, o.proposal_id)
        if p is None or p.deleted_at is not None:
            continue
        sig = s.get(Signal, p.signal_id)
        rows.append({"o": o, "p": p, "sig": sig, "name": name_of(p.isin)})
    return rows


def review_counts(s: Session, rev: LearningReview) -> dict[str, int]:
    """New items created by a review and duplicates it skipped (for the page message)."""
    return {
        "questions": len(list(s.scalars(select(LearningQuestion.id).where(LearningQuestion.review_id == rev.id)))),
        "suggestions": len(
            list(s.scalars(select(LearningSuggestion.id).where(LearningSuggestion.review_id == rev.id)))
        ),
        "doublons": sum(1 for r in (rev.rejected or []) if str(r.get("motif", "")).startswith("doublon")),
        "ecartes": sum(1 for r in (rev.rejected or []) if not str(r.get("motif", "")).startswith("doublon")),
    }
