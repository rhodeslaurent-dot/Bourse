"""A1.3 visibility: cumulative/reserved risk, gap scenario, sector, leverage on the home page."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db.session import db_session
from app.main import create_app
from app.services.portfolio import declare_execution, declare_order, declare_stop, ensure_accounts


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


def test_home_shows_three_decisions_and_risk(client):
    T = datetime(2026, 9, 15, 7, 30, tzinfo=UTC)
    with db_session() as s:
        accs = ensure_accounts(s)
        r = declare_execution(s, accs["cto_srd"].id, "FR0000120271", "buy", 100, 42.4, T, "web", mode="srd")
        declare_stop(s, r.position_id, 40.6, "seuil", 60, "initial", "web")
        declare_execution(s, accs["pea"].id, "FR0000120073", "buy", 12, 171.45, T, "web")
        declare_order(s, accs["cto_cash"].id, "NL0010273215", "buy", 3, "plage", 650.0, 640.0, "day", "web")
    html = client.get("/").text
    assert "Trois décisions du jour" in html
    assert "40 titres non protégés" in html and "FR0000120073" in html
    assert "réservé (ordres en attente)" in html and "Scénario de gap" in html and "engagements" in html
    assert "périmé ou jamais synchronisé" in html
