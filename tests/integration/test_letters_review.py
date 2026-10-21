"""docs/20 (A20.1–A20.4): daily letters review — mentions without proposals, Claude's opinions
checked by Python, deterministic recommendations, P2 on held positions, page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.data.providers.imap_gmail import NewsletterMail
from app.db.models import (
    Instrument,
    LetterOpinion,
    LetterValidation,
    NewsletterItem,
    PositionRow,
    Proposal,
    Signal,
)
from app.db.session import db_session
from app.llm.gateway import ClaudeGateway, LlmSettings
from app.llm.schemas import LetterOpinionOut, LettersReviewOut, LetterValidationOut
from app.main import create_app
from app.services.letters_review import digest_event, recommendations, run_review
from app.services.newsletters import ingest_mail
from app.services.portfolio import ensure_accounts

ASML, TTE = "NL0010273215", "FR0000120271"
NOW = datetime.now(UTC)
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
Z_TEXT = "Morning Meeting. ASML reste notre valeur préférée du secteur, nous recommandons l'achat sous 640 euros."
M_TEXT = (
    "Lettre Momentum : ASML, la dynamique commerciale reste solide. TotalEnergies : prudence, la baisse du brut pèse."
)


class FakeAlerts:
    def __init__(self):
        self.events = []

    def emit(self, s, ev, mode, intraday=True, now=None):
        self.events.append(ev)


def mail(source, mid, text, minutes_ago):
    return NewsletterMail(
        source, mid, f"Lettre {source}", f"x@{source}.fr", None, NOW - timedelta(minutes=minutes_ago), text, f"h-{mid}"
    )


@pytest.fixture
def seeded(db_engine, config, calendars):
    with db_session() as s:
        s.add(Instrument(isin=ASML, name="ASML", mic="XAMS", in_universe=True))
        s.add(Instrument(isin=TTE, name="TotalEnergies", mic="XPAR", in_universe=True))
        s.flush()
        ingest_mail(s, mail("zonebourse", "z1", Z_TEXT, 300), config, None, None, calendars=calendars)
        ingest_mail(s, mail("momentum_capital", "m1", M_TEXT, 200), config, None, None, calendars=calendars)
        s.add(
            PositionRow(
                account_id=ensure_accounts(s)["pea"].id,
                isin=TTE,
                qty_held=10,
                avg_price=55.0,
                qty_protected=10,
                opened_at=NOW,
                execution_state="filled",
                protection_state="protected",
                mode="comptant",
            )
        )
        ids = {i.source: i.id for i in s.scalars(select(NewsletterItem))}
    return ids


def test_mentions_do_not_create_proposals(seeded):
    with db_session() as s:
        props = list(s.scalars(select(Proposal)))
        sigs = {x.isin for x in s.scalars(select(Signal))}
    # « recommandons l'achat » → one proposal for ASML; the neutral TotalEnergies mention creates none
    assert TTE not in sigs and any(p.isin == ASML for p in props)


def fake_review(ids):
    def call(model, system, user, schema, temperature, max_tokens):
        assert schema is LettersReviewOut and "<dossier>" in user
        return (
            LettersReviewOut(
                synthese="Les lettres restent positives sur ASML et prudentes sur TotalEnergies.",
                avis=[
                    LetterOpinionOut(
                        name="ASML",
                        isin=ASML,
                        orientation="positive",
                        explicite=True,
                        action_lettre="achat",
                        resume="Valeur préférée du secteur.",
                        citation="ASML reste notre valeur préférée du secteur",
                        lettres=[ids["zonebourse"]],
                    ),
                    LetterOpinionOut(
                        name="ASML",
                        orientation="positive",
                        resume="Dynamique commerciale solide.",
                        citation="ASML, la dynamique commerciale reste solide",
                        lettres=[ids["momentum_capital"]],
                    ),
                    LetterOpinionOut(
                        name="TotalEnergies",
                        orientation="negative",
                        resume="Prudence, baisse du brut.",
                        citation="TotalEnergies : prudence, la baisse du brut pèse",
                        lettres=[ids["momentum_capital"]],
                    ),
                    LetterOpinionOut(name="Airbus", orientation="positive", resume="x", citation="Airbus vole haut"),
                    LetterOpinionOut(
                        name="ASML",
                        orientation="positive",
                        resume="Objectif 900 euros.",
                        citation="ASML reste notre valeur préférée du secteur",
                        lettres=[ids["zonebourse"]],
                    ),
                ],
                validations=[
                    LetterValidationOut(
                        cible="position",
                        reference=TTE,
                        verdict="contredit",
                        justification="Prudence sur le brut.",
                        lettres=[ids["momentum_capital"]],
                    ),
                    LetterValidationOut(cible="proposition", reference="9999", verdict="confirme", justification="x"),
                ],
            ),
            3000,
            700,
        )

    return call


def test_review_guards_recommendations_and_alerts(seeded, config):
    alerts = FakeAlerts()
    with db_session() as s:
        rev = run_review(s, config, ClaudeGateway(SETTINGS, call=fake_review(seeded)), alerts=alerts)
        assert rev.status == "ok" and rev.synthesis.startswith("Les lettres")
        motifs = [r["motif"] for r in rev.rejected]
        assert any("citation introuvable" in m for m in motifs)  # Airbus
        assert any("chiffres absents" in m for m in motifs)  # 900
        assert any("cible absente" in m for m in motifs)  # proposal 9999
        ops = list(s.scalars(select(LetterOpinion)))
        assert len(ops) == 3 and {o.isin for o in ops} == {ASML, TTE}
        assert len(list(s.scalars(select(LetterValidation)))) == 1
        recs = {r["isin"]: r for r in recommendations(s, config)}
        assert recs[TTE]["type"] == "vigilance" and recs[ASML]["type"] == "a_etudier"
        assert recs[ASML]["sources_positives"] == ["momentum_capital", "zonebourse"]
        ev = digest_event(rev, list(recs.values()))
        assert len(ev.telegram_text().splitlines()) <= 12 and "TotalEnergies" in ev.telegram_text()
    kinds = {e.kind: e for e in alerts.events}
    assert (
        set(kinds) == {"letter_negative_position", "letter_a_etudier"} and kinds["letter_negative_position"].isin == TTE
    )
    # nothing new since: the next review is empty and costs nothing
    with db_session() as s:
        assert run_review(s, config, ClaudeGateway(SETTINGS, call=fake_review(seeded))).status == "empty"


def test_page_and_manual_run_without_key(seeded, config, calendars, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        r = c.post("/lettres/analyser", follow_redirects=False)
        assert r.status_code == 303 and "IA%20non%20configur" in r.headers["location"]
        html = c.get("/lettres").text
        assert "Revue des lettres" in html and "Lettre zonebourse" in html and "Préconisations" in html
        assert 'href="/lettres"' in c.get("/").text


def test_review_at_reception_alerts_immediately_and_catch_up_skips(seeded, config, calendars):
    """docs/20 §20.1: letters are reviewed by imap_poll right after ingestion; P2 per new
    recommendation; the evening catch-up has nothing left to review."""
    from app.scheduler.jobs.imap_poll import review_at_reception
    from app.scheduler.runner import JobContext

    alerts = FakeAlerts()
    ctx = JobContext(config, calendars, NOW.date(), ("XPAR",), "normal")
    ctx.llm, ctx.alerts, ctx.mode = ClaudeGateway(SETTINGS, call=fake_review(seeded)), alerts, "disponible"
    with db_session() as s:
        review_at_reception(s, ctx, ["h-z1", "h-m1"])
    kinds = sorted(e.kind for e in alerts.events)
    assert kinds == ["letter_a_etudier", "letter_negative_position"]
    assert "ASML" in next(e for e in alerts.events if e.kind == "letter_a_etudier").title
    assert "réception" in ctx.note
    with db_session() as s:
        assert run_review(s, config, ClaudeGateway(SETTINGS, call=fake_review(seeded))).status == "empty"
