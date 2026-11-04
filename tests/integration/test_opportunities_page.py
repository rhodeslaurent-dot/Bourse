"""docs/17 §17.3: proposals shown with a one-line explanation and an expandable detail; web decisions
recorded with via=web; a blocked or to-verify proposal has no 'Ordre saisi' button enabled."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Decision, Instrument, Signal
from app.db.session import db_session
from app.main import create_app
from app.services.decisions import create_proposal, record_signal
from app.services.explain import explain_proposal


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def _proposal(s, blocked: bool = False, to_verify: bool = False):
    sig = record_signal(
        s,
        "FR0000120271",
        "external",
        "zonebourse",
        datetime.now(UTC),
        "eod",
        "eod",
        "2026.09.3",
        evidence={
            "direction": "buy",
            "levels": {"achat": 55.0, "stop": 52.0},
            "p_open": 55.4,
            "gap_open": 0.012,
            "drift_since_open": 0.004,
        },
        entry_low=55.0,
        stop_initial=52.0,
    )
    gates = {
        "passed": ["régime orange : taille réduite"],
        "blocked": ["BLOQUÉ : drift depuis l'ouverture +3.0% ≥ 2% (detectors.d5_external.drift_since_open_max_for_buy)"]
        if blocked
        else [],
    }
    return create_proposal(
        s,
        sig,
        "WATCH",
        {
            "FR0000120271": {
                "last": 55.3,
                "data_status": "eod",
                "market_timestamp": "2026-10-20T15:35:00+00:00",
                "source": "eodhd",
            }
        },
        gates,
        "2026.09.3",
        to_verify=to_verify,
        entry_zone={"achat": 55.0},
        stop=52.0,
        targets=[62.0],
        shares=114,
        risk_eur=342.0,
        account_recommended="pea",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )


def test_explanation_is_deterministic_and_complete():
    class P:  # minimal stand-in for the ORM rows
        action, to_verify, entry_zone, stop, targets, shares, risk_eur = (
            "WATCH",
            False,
            {"achat": 55.0},
            52.0,
            [62.0],
            114,
            342.0,
        )
        gates = {"passed": [], "blocked": ["BLOQUÉ : plafond de risque (risk.max_open_risk_pct)"]}
        account_recommended, size_eur, cost_estimate, horizon = "pea", 6270.0, {"courtage": 5.0}, "swing"

    class S:
        detector, source = "external", "zonebourse"
        evidence = {"direction": "buy", "drift_since_open": 0.004, "levels": {"achat": 55.0}}
        entry_low = 55.0

    ex = explain_proposal(P(), S(), None, capital=40000.0)
    assert "À surveiller · zonebourse (lettre / analyste)" in ex.headline
    assert (
        "stop 52.00 € (5.5% sous l'entrée)" in ex.headline
        and "114 titres" in ex.headline
        and "0.9% du capital" in ex.headline
    )
    assert ex.summary.endswith("BLOQUÉ : plafond de risque") and ex.blocked[0].startswith("BLOQUÉ")
    assert "BLOQUÉ" not in ex.headline
    assert any("Compte conseillé : PEA" in b for b in ex.bullets) and any("Coût estimé" in b for b in ex.bullets)


def test_page_lists_proposal_with_detail_and_records_web_decision(client):
    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TotalEnergies", ticker_local="TTE", mic="XPAR"))
        p = _proposal(s)
        pid = p.id
    html = client.get("/opportunites").text
    assert "TotalEnergies" in html and "À surveiller" in html and "entrée 55.00 €" in html
    assert "Données utilisées" in html and "Clôture" in html
    assert (
        'name="decision" value="order_entered"' in html
        and "disabled" not in html.split('value="order_entered"')[1][:40]
    )
    r = client.post(
        "/opportunites/decision",
        data={"proposal_id": pid, "decision": "watch", "reason": "attendre le repli"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with db_session() as s:
        d = s.scalar(select(Decision).where(Decision.proposal_id == pid))
        assert d is not None and d.via == "web" and d.reason == "attendre le repli"
        sig = s.scalar(select(Signal))
        assert sig is not None
    assert "Watch" in client.get("/opportunites").text
    assert client.post("/opportunites/decision", data={"proposal_id": 999, "decision": "seen"}).status_code == 404
    assert client.post("/opportunites/decision", data={"proposal_id": pid, "decision": "buy"}).status_code == 400


def test_blocked_or_to_verify_disables_order_entered(client):
    with db_session() as s:
        _proposal(s, blocked=True, to_verify=True)
    html = client.get("/opportunites").text
    assert "BLOQUÉ : drift" in html and "à vérifier" in html
    assert "disabled" in html.split('value="order_entered"')[1][:80]


def test_empty_day_is_a_normal_result(client):
    assert "absence de proposition = résultat normal" in client.get("/opportunites?day=2026-01-05").text


def test_a_card_shows_one_decision_its_reason_and_what_to_do_first(client):
    """Lot R2 (docs/38, user feedback of 02/11/2026): one label, « Pourquoi » and « Que faire » before the folded
    analysis; the former labels stay inside the fold."""
    import html as h

    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TotalEnergies", ticker_local="TTE", mic="XPAR"))
        _proposal(s, blocked=True)
    page = h.unescape(client.get("/opportunites").text)
    card = page[page.index('<section id="p') :]
    assert "REFUS" in card
    why, todo, fold = card.index("Pourquoi :"), card.index("Que faire :"), card.index("data-card-analysis")
    assert why < todo < fold and "Rien à faire" in card[todo:fold]
    assert "À surveiller" in card[fold:]  # the engine's own label, folded


def test_an_observation_card_shows_no_quantity_and_no_order_button(client):
    """Decision of 04/11/2026 (docs/39): a measured negative edge — the card says « OBSERVATION », shows no quantity,
    and « Ordre saisi » is disabled; the levels stay for the journal."""
    import html as h

    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TotalEnergies", ticker_local="TTE", mic="XPAR"))
        p = _proposal(s)
        p.gates = {"passed": [], "blocked": ["WATCH : observation (espérance mesurée -0.25 R, backtest, entrée et "
                                             "sorties de production, n 4407) — aucun ordre tant que la mesure n'est "
                                             "pas positive (docs/39)"]}  # fmt: skip
        p.details = {**(p.details or {}), "observation": {"r": -0.25, "n": 4407, "source": "backtest"}}
    page = h.unescape(client.get("/opportunites").text)
    card = page[page.index('<section id="p') :]
    assert "OBSERVATION" in card and "data-card-observation" in card and "114 titres" not in card
    assert "disabled" in card.split('value="order_entered"')[1][:120]
    assert "Rien à saisir" in card


def test_the_card_says_its_sector_source_its_series_cut_and_an_old_cash(client):
    """04/11/2026 (analysis of the cards: « Inwido : Non-Energy Minerals », « Amper : zones d'avant la
    restructuration », « cash 11 jours ») — the screener's sector translated with its source, the day the series
    counts from, the cash of the recommended account when it is old."""
    import html as h

    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TotalEnergies", ticker_local="TTE", mic="XPAR",
                         sector="Non-Energy Minerals", industry="Construction Materials"))  # fmt: skip
        p = _proposal(s)
        p.details = {**(p.details or {}),
                     "serie_coupee": {"jour": "2026-06-02", "motif": "opération sur titre (facteur d'ajustement)",
                                      "seances_ecartees": 120},
                     "liquidites": {"compte": "pea", "recent": False, "age_jours": 11.0}}  # fmt: skip
    page = h.unescape(client.get("/opportunites").text)
    card = page[page.index('<section id="p') :]
    assert "Matériaux (métaux, mines, matériaux de construction) · Construction Materials (classification" in card
    assert "Historique lu depuis le 02/06/2026 (opération sur titre" in card and "120 séances" in card
    assert "Liquidités du compte PEA relevées il y a 11 jours" in card
