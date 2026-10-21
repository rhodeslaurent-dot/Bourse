"""docs/18 (A18.2–A18.7): outcomes on stored bars, comparable history and BUY → WATCH, AI review
guards, questions / suggestions / lessons, page and assistant panel with page facts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    AssistantMessage,
    Decision,
    DecisionSnapshot,
    Instrument,
    LearningQuestion,
    LearningSuggestion,
    PriceEod,
    SignalOutcome,
    TraderMemory,
)
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.llm.schemas import (
    AssistantAnswer,
    LearningFinding,
    LearningQuestionOut,
    LearningReviewOut,
    LearningSuggestionOut,
)
from app.main import create_app
from app.services.decisions import create_proposal, record_decision, record_signal
from app.services.learning import aggregates, answer_question, decide_suggestion, run_review, yaml_snippet
from app.services.outcomes import samples, update_outcomes

ISIN = "FR0000120271"
NOW = datetime(2026, 10, 21, 7, 30, tzinfo=UTC)
SETTINGS = LlmSettings(
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    0,
    15,
    5,
    {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}},
    Path("app/llm/prompts"),
    False,
)


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def _bars(calendars, start: date, rows: list[tuple[float, float, float, float]]) -> None:
    cal = calendars.get("XPAR")
    days = cal.sessions_between(start, start + timedelta(days=60))[: len(rows)]
    with db_session() as s:
        for d, (o, h, lo, c) in zip(days, rows, strict=False):
            s.add(
                PriceEod(
                    isin=ISIN,
                    day=d,
                    open=o,
                    high=h,
                    low=lo,
                    close=c,
                    volume=1000,
                    source="eodhd",
                    received_at=NOW,
                    data_status="eod",
                    official=True,
                )
            )


def _proposal(
    s,
    created: datetime,
    action="WATCH",
    entry=100.0,
    stop=95.0,
    target=110.0,
    detector="external",
    source="zonebourse",
    config=None,
):  # noqa: E501
    sig = record_signal(
        s, ISIN, detector, source, created, "eod", "eod", "test", entry_low=entry, stop_initial=stop, targets=[target]
    )
    p = create_proposal(
        s,
        sig,
        action,
        None,
        {"passed": [], "blocked": []},
        "test",
        config=config,
        entry_zone={"achat": entry},
        stop=stop,
        targets=[target],
    )
    p.created_at = created
    return p


def test_outcomes_on_stored_bars_and_decision_groups(db_engine, config, calendars):
    with db_session() as s:
        s.add(Instrument(isin=ISIN, name="TotalEnergies", mic="XPAR", in_universe=True))
    # created Thu 01/10 at 14:00 Paris; the 01/10 bar must be ignored (it touches the stop)
    _bars(
        calendars,
        date(2026, 10, 1),
        [
            (99, 100, 90, 91),
            (103, 104, 101, 103),
            (102, 102.5, 99.5, 101),
            (101, 106, 100.5, 105),
            (106, 111, 105, 110),
        ],
    )
    created = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    with db_session() as s:
        taken = _proposal(s, created)
        ignored = _proposal(s, created)
        record_decision(s, taken.id, "order_entered", "web")
        record_decision(s, ignored.id, "ignored", "web")
        n = update_outcomes(s, config, calendars, NOW)
        assert n == 2
        outs = {o.proposal_id: o for o in s.scalars(select(SignalOutcome))}
        o = outs[taken.id]
        assert o.status == "target" and o.final and o.entry_day == date(2026, 10, 5) and o.decision_group == "prise"
        assert outs[ignored.id].decision_group == "ignoree"
        ss = samples(s)
        assert len(ss) == 2 and all(x.r and x.r > 1.5 for x in ss)
        agg = aggregates(s, config)
        assert agg["echantillon"]["resultats_definitifs"] == 2 and agg["global"]["fiabilite"] == "insuffisant"
        assert {r["groupe"] for r in agg["par_decision"]} == {"Prise", "Ignorée"}
        # finals are not recomputed
        assert update_outcomes(s, config, calendars, NOW) == 0


def test_comparable_history_demotes_buy_and_is_frozen(db_engine, config, calendars):
    with db_session() as s:
        s.add(Instrument(isin=ISIN, name="TotalEnergies", mic="XPAR", in_universe=True))
        base = datetime(2026, 3, 2, 12, tzinfo=UTC)
        for i in range(60):
            p = _proposal(s, base + timedelta(days=i), detector="d4", source="scan")
            s.add(
                SignalOutcome(
                    proposal_id=p.id,
                    signal_id=p.signal_id,
                    isin=ISIN,
                    computed_at=NOW,
                    status="stopped",
                    final=True,
                    decision_group="ignoree",
                    r_gross=-1.0,
                    r_net=-1.0,
                    exit_day=(base + timedelta(days=i + 3)).date(),
                    last_bar_day=(base + timedelta(days=i + 3)).date(),
                )
            )
        s.flush()
        new = _proposal(s, NOW, action="BUY", detector="d4", source="scan", config=config)
        assert new.action == "WATCH"
        assert any("historique défavorable" in b and "learning.demote_buy_to_watch" in b for b in new.gates["blocked"])
        snap = s.scalar(select(DecisionSnapshot).where(DecisionSnapshot.proposal_id == new.id))
        tr = snap.features["track_record"]
        assert tr["n"] == 60 and tr["verdict"] == "defavorable" and tr["scope"] == "détecteur + source"


def _fake_review(model, system, user, schema, temperature, max_tokens):
    assert schema is LearningReviewOut and "<dossier>" in user and "seuils_candidats" in user
    return (
        LearningReviewOut(
            summary="Peu de résultats définitifs : prudence.",
            findings=[
                LearningFinding(title="Lettre utile", detail="Espérance de 7,35 R sur la source.", confidence="faible"),
                LearningFinding(title="Échantillon faible", detail="Fiabilité insuffisante.", confidence="faible"),
            ],
            questions=[
                LearningQuestionOut(
                    question="Pourquoi ignorez-vous les signaux reçus après 10 h ?",
                    why="Distinguer contrainte d'agenda et choix.",
                    kind="choix",
                    choices=["Indisponible", "Prix trop loin", "Autre"],
                )
            ],
            suggestions=[
                LearningSuggestionOut(
                    kind="parametre",
                    title="Abaisser la dérive maximale",
                    rationale="Tranche défavorable au-delà.",
                    expected_effect="Moins de BUY tardifs.",
                    params_key="detectors.d5_external.drift_since_open_max_for_buy",
                    proposed_value=0.01,
                ),
                LearningSuggestionOut(
                    kind="parametre",
                    title="Clé inventée",
                    rationale="x",
                    expected_effect="x",
                    params_key="detectors.nope",
                    proposed_value=0.01,
                ),
                LearningSuggestionOut(
                    kind="parametre",
                    title="Plus de risque",
                    rationale="x",
                    expected_effect="x",
                    params_key="risk.max_open_risk_pct",
                    proposed_value=0.06,
                ),
                LearningSuggestionOut(
                    kind="processus",
                    title="Décider avant 10 h",
                    rationale="Les signaux tardifs sont moins bons.",
                    expected_effect="Moins de dérive.",
                ),
            ],
        ),
        2000,
        600,
    )


def test_review_guards_questions_suggestions_and_memory(db_engine, config, calendars):
    with db_session() as s:
        rules = run_review(s, config, None)
        assert rules.status == "rules" and "aucun résultat définitif" in rules.summary
        rev = run_review(s, config, ClaudeGateway(SETTINGS, call=_fake_review))
        assert rev.status == "ok" and rev.cost_usd > 0
        assert [f["title"] for f in rev.findings] == ["Échantillon faible"]  # 7,35 is not in the dossier
        motifs = " ".join(r["motif"] for r in rev.rejected)
        assert "clé inconnue" in motifs and "plafond de risque" in motifs and "chiffres absents" in motifs
        sugs = {x.title: x for x in s.scalars(select(LearningSuggestion))}
        assert set(sugs) == {"Abaisser la dérive maximale", "Décider avant 10 h"}
        drift = sugs["Abaisser la dérive maximale"]
        assert drift.current_value == 0.02  # read by Python from params.yaml
        q = s.scalar(select(LearningQuestion))
        assert q.kind == "choix" and q.status == "open"
        # same question is not asked twice
        run_review(s, config, ClaudeGateway(SETTINGS, call=_fake_review))
        assert len(list(s.scalars(select(LearningQuestion)))) == 1

        answer_question(s, q.id, "Indisponible — réunions le matin")
        decide_suggestion(s, sugs["Décider avant 10 h"].id, "accepted", "")
        decide_suggestion(s, drift.id, "accepted", "à tester un mois")
        texts = [m.text for m in s.scalars(select(TraderMemory))]
        assert any("réunions le matin" in t for t in texts) and any("Décider avant 10 h" in t for t in texts)
        snippet = yaml_snippet(drift)
        assert "drift_since_open_max_for_buy: 0.01" in snippet and "était 0.02" in snippet
    assert "drift_since_open_max_for_buy: 0.02" in Path("config/params.example.yaml").read_text(encoding="utf-8")


def test_learning_page_and_assistant_panel(client, calendars):
    with db_session() as s:
        s.add(Instrument(isin=ISIN, name="TotalEnergies", mic="XPAR", in_universe=True))
        p = _proposal(s, datetime.now(UTC) - timedelta(minutes=5))
        s.add(TraderMemory(ts=NOW, text="Ne jamais acheter un gap de plus de 4 %", source="utilisateur", active=True))
        s.add(Decision(proposal_id=p.id, decision="seen", via="web", ts=NOW))
    html = client.get("/apprentissage").text
    assert "Apprentissage" in html and "Leçons retenues" in html and "Ne jamais acheter un gap" in html
    r = client.post("/apprentissage/recalculer", follow_redirects=False)
    assert r.status_code == 303 and "recalcul" in r.headers["location"]
    page = client.get("/apprentissage").text
    assert "En attente de séance" in page or "En cours" in page
    assert "Cours indisponibles" not in page
    r = client.post("/apprentissage/analyse", follow_redirects=False)
    assert r.status_code == 303
    assert (
        client.post("/apprentissage/lecon", data={"text": "Relire le samedi"}, follow_redirects=False).status_code
        == 303
    )

    for path in ("/", "/opportunites", "/positions", "/performance", "/apprentissage", f"/valeur/{ISIN}"):
        body = client.get(path).text
        assert 'id="ai-panel"' in body and "/static/assistant.js" in body
    assert f'name="isin" value="{ISIN}"' in client.get(f"/valeur/{ISIN}").text
    assert f'data-ai-isin="{ISIN}"' in client.get("/opportunites").text

    frag = client.get("/assistant/panel", params={"page": "/opportunites"}).text
    assert "Résume les opportunités du jour." in frag and "<html" not in frag
    frag = client.post("/assistant/panel", data={"question": "Résume", "page": "/opportunites", "isin": ""}).text
    assert "Résume" in frag and "données seules" in frag
    with db_session() as s:
        m = s.scalar(select(AssistantMessage).order_by(AssistantMessage.id.desc()))
        assert m.page == "/opportunites"
        assert m.context["page"]["propositions_du_jour"][0]["isin"] == ISIN
        assert "Relire le samedi" in m.context["lecons_retenues"]
    # without JavaScript the form posts to /assistant with the page
    r = client.post("/assistant", data={"question": "Quoi ?", "page": "/performance"}, follow_redirects=False)
    assert r.status_code == 303
    with db_session() as s:
        m = s.scalar(select(AssistantMessage).order_by(AssistantMessage.id.desc()))
        assert m.page == "/performance" and "synthese" in m.context["page"]


def test_assistant_continuity_uses_previous_answers(db_engine, config):
    from app.services.assistant import ask

    calls = []

    def fake(model, system, user, schema, temperature, max_tokens):
        calls.append(user)
        return AssistantAnswer(answer="Rien à signaler."), 500, 40

    gw = ClaudeGateway(SETTINGS, call=fake)
    with db_session() as s:
        ask(s, gw, "Première question", None, 40000.0, page="/positions", config=config)
        ask(s, gw, "Et ensuite ?", None, 40000.0, page="/positions", config=config)
    assert "echanges_precedents" not in calls[0] and "Première question" in calls[1]
