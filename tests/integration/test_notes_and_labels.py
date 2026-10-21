"""docs/17 §17.2 and §17.7: French labels on pages, notes saved in place and shown again."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Note
from app.db.session import db_session
from app.main import create_app
from app.services.notes import set_note
from app.services.portfolio import declare_execution, declare_stop, ensure_accounts

T = datetime(2026, 10, 20, 7, 30, tzinfo=UTC)


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def test_positions_show_labels_not_identifiers(client):
    with db_session() as s:
        accs = ensure_accounts(s)
        r = declare_execution(s, accs["cto_srd"].id, "FR0000120271", "buy", 100, 42.4, T, "web", mode="srd")
        declare_stop(s, r.position_id, 40.6, "seuil", 60, "initial", "web")
    html = client.get("/positions").text
    assert "Partiellement protégée" in html and "Exécutée" in html and "CTO SRD" in html
    assert 'title="partially_protected"' in html  # identifier kept as tooltip for the API vocabulary


def test_note_is_saved_and_shown_again(client):
    with db_session() as s:
        accs = ensure_accounts(s)
        r = declare_execution(s, accs["pea"].id, "FR0000120073", "buy", 12, 171.45, T, "web")
        pid = r.position_id
    resp = client.post(
        "/notes",
        data={"scope": "position", "ref": str(pid), "text": "Thèse : rebond sur MM50, stop 165", "next": "/positions"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/positions"
    assert "rebond sur MM50" in client.get("/positions").text
    # edited in place (one row), never deleted
    client.post("/notes", data={"scope": "position", "ref": str(pid), "text": "", "next": "/positions"})
    with db_session() as s:
        rows = list(s.scalars(select(Note).where(Note.scope == "position")))
        assert len(rows) == 1 and rows[0].text == ""
    assert client.post("/notes", data={"scope": "bogus", "ref": "1", "text": "x"}).status_code == 400
    assert (
        client.post(
            "/notes", data={"scope": "global", "ref": "todo", "text": "x", "next": "//evil"}, follow_redirects=False
        ).headers["location"]
        == "/"
    )


def test_watchlist_note_and_reason_label(client):
    with db_session() as s:
        from app.db.models import Instrument

        s.add(Instrument(isin="FR0000120271", name="TotalEnergies", ticker_local="TTE", mic="XPAR"))
    client.post(
        "/watchlist/add", data={"isin": "FR0000120271", "reason": "a_l_affut", "note": ""}, follow_redirects=False
    )
    with db_session() as s:
        set_note(s, "watchlist", "FR0000120271", "Attendre le repli vers 55")
    html = client.get("/watchlist").text
    assert "À l’affût (repli)" in html and "Attendre le repli vers 55" in html and "Force relative" in html
