"""Lot R1 (docs/38) — « Revue du jour », the home page: the verdict first, what the user must do by urgency, one
block per idea with one decision, the order book, the portfolio with the date of its last quote, the managers'
mentions as watchlist entries; the same content as text (e-mail) and in at most 12 Telegram lines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import Instrument, MediaMention
from app.db.session import db_session
from app.domain.timeutil import PARIS
from app.main import create_app
from app.services import daily_review
from app.services.decisions import create_proposal, record_signal
from app.services.portfolio import declare_execution, declare_stop, ensure_accounts

PUB, ELIOR, BUYV, STALE = "FR0000130577", "FR0011950732", "FR0000120271", "FR0000121972"
PULLBACK = "WATCH : refusé — P_max 98.08 dépassé de 2.01% — limite posée en attente d'un repli"


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    for var in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "TELEGRAM_BOT_TOKEN", "EODHD_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def _idea(s, isin: str, action: str, blocked: list[str], price: float, p_max: float, stop: float, targets, *,
          letter: dict | None = None, to_verify: bool = False, shares: int = 50):  # fmt: skip
    sig = record_signal(s, isin, "external" if letter else "technical", "momentum_capital" if letter else "d7",
                        datetime.now(UTC), "eod", "eod", "2026.11.1", evidence={})  # fmt: skip
    details = {"p_max": p_max, "comptes": {"pea": {"titres": shares}}}
    if letter:
        details["lettre"] = letter
    return create_proposal(
        s, sig, action, {isin: {"last": price, "data_status": "delayed"}}, {"passed": [], "blocked": blocked},
        "2026.11.1", to_verify=to_verify, stop=stop, targets=targets, account_recommended="pea", details=details,
        expires_at=datetime.now(UTC) + timedelta(days=2),
    )  # fmt: skip


def _seed():
    with db_session() as s:
        for isin, name in ((PUB, "Publicis Groupe SA"), (ELIOR, "Elior SCA"), (BUYV, "TotalEnergies SE"),
                           (STALE, "Schneider Electric")):  # fmt: skip
            s.add(Instrument(isin=isin, name=name, mic="XPAR", currency="EUR", in_universe=True, pea_eligible=True))
        s.flush()
        _idea(s, PUB, "WATCH", [PULLBACK], 100.05, 98.08, 97.66, [103.1, 104.85],
              letter={"source": "momentum_capital", "sens": "buy", "niveaux": {"objectif": 105.2},
                      "citation": "On visera dans un premier temps le sommet historique à 105,20 euros."})  # fmt: skip
        _idea(s, ELIOR, "WATCH", ["WATCH : stop trop large (9.4% sous l'entrée)", PULLBACK], 1.887, 1.762, 1.692,
              [1.957, 2.054], letter={"source": "momentum_capital", "sens": "watch",
                                      "niveaux": {"declencheur": 1.87, "objectif": 2.05, "stop": 1.76}})  # fmt: skip
        _idea(s, BUYV, "BUY", [], 85.0, 85.5, 80.72, [92.0], shares=40)
        accs = ensure_accounts(s)
        t = datetime.now(UTC) - timedelta(days=1)
        r = declare_execution(s, accs["pea"].id, STALE, "buy", 34, 238.35, t, "web")
        declare_stop(s, r.position_id, 232.0, "seuil", 20, "initial", "web")  # 14 shares left unprotected
        s.add(MediaMention(source="cva_portefeuille_gerants", item_guid="w45", name="Schneider Electric",
                           isin=STALE, stance="achat", speaker="Sébastien Korchia", published_at=datetime.now(UTC),
                           received_at=datetime.now(UTC)))  # fmt: skip
        s.add(MediaMention(source="cva_portefeuille_gerants", item_guid="w45", name="Publicis", isin=PUB,
                           stance="achat", speaker="Gérant B", published_at=datetime.now(UTC),
                           received_at=datetime.now(UTC)))  # fmt: skip


def test_the_review_puts_the_decisions_first(config, db_engine):
    _seed()
    day = datetime.now(PARIS).date()
    with db_session() as s:
        r = daily_review.build(s, config, day)
    assert r["verdict"]["titre"] == "1 achat(s) possible(s) aujourd'hui"
    by = {i["isin"]: i for i in r["fiches"]}
    assert by[BUYV]["decision"] == "acheter" and by[BUYV]["que_faire"].startswith(
        "Poser l'ordre limite à 85.5 (40 titres"
    )
    assert by[PUB]["decision"] == "attendre_repli" and "100.05" in by[PUB]["raison"]
    assert by[PUB]["niveaux_source"] == ["objectif 1 105.2"] and by[PUB]["citation"].startswith("On visera")
    assert by[ELIOR]["decision"] == "inexploitable" and "1.87" in by[ELIOR]["raison"]
    assert by[PUB]["objectifs"][0]["rr_brut"] is not None  # gross table at the price of the analysis
    # what the user must do: the unprotected shares first, then the order to post
    asks = [a["texte"] for a in r["attentes"]]
    assert asks[0].startswith("Saisir un stop") and "14 titres non protégés" in asks[0]
    assert any(a.startswith("TotalEnergies SE : Poser l'ordre limite") for a in asks)
    assert any("non mis à jour" in a for a in asks)  # no stored quote for the held value: said, never assumed
    book = {b["nom"]: b for b in r["carnet"]}
    assert (
        book["TotalEnergies SE"]["statut"] == "à poser"
        and book["Publicis Groupe SA"]["statut"] == "prête pour un repli"
    )
    assert "Elior SCA" not in book  # inexploitable: no order
    ex = {e["valeur"]: e for e in r["experts"]}
    assert "liste de surveillance" in ex["Schneider Electric"]["traitement"]
    assert "confirme la fiche du jour" in ex["Publicis Groupe SA"]["traitement"]
    txt = daily_review.as_text(r)
    assert txt.index("VERDICT") < txt.index("CE QUE J'ATTENDS DE TOI") < txt.index("FICHES DU JOUR")
    assert "Elior SCA — INEXPLOITABLE" in txt and "Aucun ordre n'est passé par l'outil" in txt
    tg = daily_review.telegram_lines(r)
    assert len(tg) <= 12 and tg[0] == r["verdict"]["titre"]


def test_the_home_page_is_the_review_and_the_dashboard_moved(client):
    _seed()
    html = client.get("/").text
    for marker in ("data-review-verdict", "Ce que j'attends de toi", "Fiches du jour", "Carnet d'ordres",
                   "Sources expertes", "ATTENDRE UN REPLI", "INEXPLOITABLE", "ACHETER", "Que faire"):  # fmt: skip
        assert marker in html, marker
    assert "Revue du jour" in html and 'href="/decisions"' in html
    assert "Trois décisions du jour" in client.get("/decisions").text


def test_an_empty_day_says_so(client):
    import html as h

    html = h.unescape(client.get("/").text)
    assert "Aucune idée aujourd'hui" in html and "Rien à faire aujourd'hui." in html


def test_the_morning_job_sends_the_review_first(config, calendars, db_engine, monkeypatch):
    from app.scheduler.jobs import daily_report
    from app.scheduler.runner import JobContext

    _seed()
    sent: list[tuple[str, str]] = []
    emitted: list = []

    class Alerts:
        def emit(self, s, event, mode, force):
            emitted.append(event)

    import app.notify.email as email

    monkeypatch.setattr(email, "smtp_settings", lambda: object())
    monkeypatch.setattr(email, "send_email", lambda subject, body: sent.append((subject, body)))
    day = datetime.now(PARIS).date()
    ctx = JobContext(config, calendars, day, ("XPAR",), "default", alerts=Alerts())
    assert daily_report.run(ctx) == 10
    assert emitted and emitted[0].title.startswith("Revue du") and len(emitted[0].lines) <= 12
    subject, body = sent[0]
    assert "1 achat(s) possible(s)" in subject
    assert body.index("VERDICT") < body.index("Rapport du")  # the review first, the 10 rubrics after


def test_unvalidated_cards_can_be_shown_for_information_only(config, db_engine):
    """docs/39, decision D3: « information » moves the cards of sources no measure validated out of the day's ideas
    (folded section, same decision and reason); the default « achat » changes nothing."""
    import copy

    _seed()
    day = datetime.now(PARIS).date()
    with db_session() as s:
        assert daily_review.build(s, config, day)["fiches_information"] == []  # default: unchanged
        c = copy.deepcopy(config)
        c.params.allocation["unvalidated_cards"] = "information"
        r = daily_review.build(s, c, day)
        assert r["fiches"] == [] and len(r["fiches_information"]) == 3
        assert r["verdict"]["titre"] == "Aucun achat validé par la mesure aujourd'hui" and r["carnet"] == []
        assert r["verdict"]["raisons"][0].startswith("3 fiche(s) non validée(s) par la mesure")
        assert "À TITRE D'INFORMATION" in daily_review.as_text(r)
        c.params.allocation["validated_sources"] = ["d7"]  # the technical card's detector validated: it comes back
        r = daily_review.build(s, c, day)
        assert [i["nom"] for i in r["fiches"]] == ["TotalEnergies SE"] and len(r["fiches_information"]) == 2


def test_a_block_outside_the_euro_says_its_currency(config, db_engine):
    """04/11/2026 (analysis of the cards: « DKK labelled € ») — the prices of a block are in the listing's currency;
    the block's name says it when it is not the euro (page, e-mail, Telegram, asks)."""
    with db_session() as s:
        s.add(Instrument(isin="DK0000000DK1", name="Danoise fictive", mic="XCSE", currency="DKK", in_universe=True))
        s.flush()
        _idea(s, "DK0000000DK1", "BUY", [], 640.0, 645.0, 600.0, [700.0])
        r = daily_review.build(s, config, datetime.now(PARIS).date())
    blk = next(i for i in r["fiches"] if i["isin"] == "DK0000000DK1")
    assert blk["nom"].endswith("(cours en DKK)") and "(cours en DKK)" in daily_review.as_text(r)
