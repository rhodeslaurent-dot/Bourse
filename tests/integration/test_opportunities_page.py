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
