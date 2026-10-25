"""``/apprentissage`` (docs/18 §18.7): outcomes of past proposals, statistics, AI questions and
suggestions, lessons. Every figure comes from ``app/services/learning.py`` (Python)."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import LearningQuestion, LearningReview, LearningSuggestion, PriceEod, TraderMemory
from app.db.session import db_session
from app.services.learning import (
    add_memory,
    adr_draft,
    aggregates,
    answer_question,
    decide_suggestion,
    outcome_rows,
    review_counts,
    run_review,
    yaml_snippet,
)
from app.services.outcomes import learning_params, update_outcomes
from app.web.charts import line_chart
from app.web.routes import templates

router = APIRouter()


def _back(msg: str = "", anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/apprentissage{'?msg=' + quote(msg) if msg else ''}{anchor}", status_code=303)


def _outcome_chart(s, row) -> str:  # type: ignore[no-untyped-def]
    o, p = row["o"], row["p"]
    start = p.created_at.date() - timedelta(days=10)
    end = (o.exit_day or o.last_bar_day or p.created_at.date()) + timedelta(days=5)
    closes = list(
        s.scalars(
            select(PriceEod)
            .where(PriceEod.isin == p.isin, PriceEod.day >= start, PriceEod.day <= end)
            .order_by(PriceEod.day)
        )
    )
    if len(closes) < 2:
        return ""
    levels = {}
    if o.ref_price:
        levels["entrée"] = o.ref_price
    if p.stop:
        levels["stop"] = p.stop
    if p.targets and isinstance(p.targets[0], int | float):
        levels["objectif"] = float(p.targets[0])
    return line_chart({"main": [(c.day, c.close) for c in closes]}, levels, height=180)


def _observation(s) -> dict:  # type: ignore[no-untyped-def]
    """Rules under measurement before any use (docs/24, docs/26, docs/27): latest backtest, committee
    verdicts against the outcomes, radar labels of the last 30 days."""
    from datetime import date, timedelta

    from sqlalchemy import func

    from app.db.models import BacktestRun, WeakSignalHit
    from app.services.committee import measure as committee_measure

    run = s.scalar(select(BacktestRun).order_by(BacktestRun.id.desc()))
    segs = ((run.summary or {}).get("segments") or {}) if run else {}
    since = date.today() - timedelta(days=30)
    radar = dict(
        s.execute(
            select(WeakSignalHit.label, func.count(WeakSignalHit.id))
            .where(WeakSignalHit.day >= since)
            .group_by(WeakSignalHit.label)
        ).all()
    )
    return {
        "backtest": run,
        "segments": {k: v for k, v in segs.items() if k.startswith(("production:", "periode:"))},
        "essais": (run.summary or {}).get("essais") if run else None,
        "committee": committee_measure(s),
        "radar": radar,
    }


@router.get("/apprentissage", response_class=HTMLResponse)
def learning_page(request: Request) -> HTMLResponse:
    st = request.app.state
    cfg = st.config
    enabled = learning_params(cfg) is not None
    ctx: dict = {"request": request, "enabled": enabled, "llm_on": bool(getattr(st, "llm", None) and st.llm.enabled)}
    if enabled:
        with db_session() as s:
            ctx["agg"] = aggregates(s, cfg)
            ctx["review"] = s.scalar(select(LearningReview).order_by(LearningReview.id.desc()))
            ctx["questions"] = list(
                s.scalars(
                    select(LearningQuestion).where(LearningQuestion.status == "open").order_by(LearningQuestion.id)
                )
            )
            ctx["suggestions"] = list(
                s.scalars(
                    select(LearningSuggestion)
                    .where(LearningSuggestion.status.in_(["open", "later"]))
                    .order_by(LearningSuggestion.status.desc(), LearningSuggestion.id)  # open before later
                )
            )
            decided = list(
                s.scalars(
                    select(LearningSuggestion)
                    .where(LearningSuggestion.status.in_(["accepted", "rejected"]))
                    .order_by(LearningSuggestion.decided_at.desc())
                    .limit(15)
                )
            )
            ctx["decided"] = [{"x": d, "yaml": yaml_snippet(d), "adr": adr_draft(d)} for d in decided]
            ctx["memory"] = list(
                s.scalars(select(TraderMemory).order_by(TraderMemory.active.desc(), TraderMemory.id.desc()))
            )
            ctx["observation"] = _observation(s)
            rows = outcome_rows(s, 60)
            for i, r in enumerate(rows):
                r["chart"] = _outcome_chart(s, r) if i < 30 else ""
            ctx["rows"] = rows
    return templates.TemplateResponse(request, "apprentissage.html", ctx)


def _require(request: Request) -> None:
    if learning_params(request.app.state.config) is None:
        raise HTTPException(409, "groupe learning absent de params.yaml : fonctionnalité désactivée")


@router.post("/apprentissage/recalculer")
def recompute(request: Request) -> RedirectResponse:
    _require(request)
    st = request.app.state
    with db_session() as s:
        n = update_outcomes(s, st.config, st.calendars)
    return _back(f"{n} résultat(s) recalculé(s)")


@router.post("/apprentissage/analyse")
def analyse(request: Request) -> RedirectResponse:
    _require(request)
    st = request.app.state
    with db_session() as s:
        update_outcomes(s, st.config, st.calendars)
        rev = run_review(s, st.config, getattr(st, "llm", None), trigger="manual")
        msg = {
            "ok": "Analyse IA terminée",
            "rules": "IA non configurée : statistiques seules",
            "unavailable": "IA indisponible : statistiques seules (raison sous « Dernière analyse »)",
        }.get(rev.status, rev.status)
        if rev.status == "ok":
            c = review_counts(s, rev)
            if c["questions"] or c["suggestions"]:
                msg += f" : {c['questions']} nouvelle(s) question(s), {c['suggestions']} nouvelle(s) proposition(s)"
            else:
                msg += " : rien de nouveau (les données n'ont pas changé depuis la dernière analyse)"
            if c["doublons"]:
                msg += f" · {c['doublons']} doublon(s) écarté(s)"
    return _back(msg)


@router.post("/apprentissage/question/{qid}")
def answer(request: Request, qid: int, answer: str = Form(""), choice: str = Form("")) -> RedirectResponse:
    _require(request)
    text = " — ".join(x for x in (choice.strip(), answer.strip()) if x)
    with db_session() as s:
        try:
            answer_question(s, qid, text)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
    return _back("Réponse enregistrée dans les leçons" if text else "Question écartée", "#questions")


@router.post("/apprentissage/suggestion/{sid}")
def decide(request: Request, sid: int, status: str = Form(...), comment: str = Form("")) -> RedirectResponse:
    _require(request)
    with db_session() as s:
        try:
            decide_suggestion(s, sid, status, comment)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return _back(
        {"accepted": "Proposition acceptée", "rejected": "Proposition refusée", "later": "Reportée"}[status],
        "#propositions",
    )


@router.post("/apprentissage/lecon")
def add_lesson(request: Request, text: str = Form("")) -> RedirectResponse:
    _require(request)
    with db_session() as s:
        add_memory(s, text, "utilisateur")
    return _back("Leçon ajoutée" if text.strip() else "", "#lecons")


@router.post("/apprentissage/lecon/{mid}/basculer")
def toggle_lesson(request: Request, mid: int) -> RedirectResponse:
    _require(request)
    with db_session() as s:
        m = s.get(TraderMemory, mid)
        if m is None:
            raise HTTPException(404, "leçon inconnue")
        m.active = not m.active
    return _back("", "#lecons")
