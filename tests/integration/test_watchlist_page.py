import pytest
from fastapi.testclient import TestClient

from app.db.models import Instrument
from app.db.session import db_session
from app.main import create_app


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    for var in (
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUD",
        "TELEGRAM_BOT_TOKEN",
        "HEALTHCHECKS_PING_KEY",
        "EODHD_API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    app = create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def test_watchlist_add_remove_export_and_instrument_api(client):
    with db_session() as s:
        s.add(
            Instrument(
                isin="FR0000120271",
                name="TotalEnergies",
                mic="XPAR",
                ticker_local="TTE",
                ticker_tv="EURONEXT:TTE",
                pea_eligible=True,
                pea_confidence=0.8,
                pea_source="domicile",
                srd_status="complet",
                in_universe=True,
            )
        )
    assert "Watchlist vide" in client.get("/watchlist").text
    r = client.post(
        "/watchlist/add",
        data={"isin": "fr0000120271", "reason": "external", "note": "Zonebourse"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    html = client.get("/watchlist").text
    assert "TotalEnergies" in html and "à confirmer chez Saxo" in html
    assert client.get("/watchlist/export.txt").text == "EURONEXT:TTE"
    info = client.get("/api/instruments/FR0000120271").json()
    assert (
        info["pea"]["eligible_regulatory"] is True
        and info["srd"]["status"] == "complet"
        and "à confirmer" in info["srd"]["label"]
    )
    assert client.get("/api/instruments/XX0000000000").status_code == 404
    client.post("/watchlist/remove", data={"isin": "FR0000120271"}, follow_redirects=False)
    assert "Watchlist vide" in client.get("/watchlist").text
    assert client.post("/watchlist/add", data={"isin": "bad"}, follow_redirects=False).status_code == 400
