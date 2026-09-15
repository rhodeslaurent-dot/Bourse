"""API declarations, /positions page, CSV import, Telegram declaration commands."""

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

FX = Path(__file__).resolve().parents[1] / "fixtures" / "csv"


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


def test_order_then_execution_then_protection_flow(client):
    r = client.post(
        "/api/orders",
        json={
            "account": "cto_srd",
            "isin": "FR0000120271",
            "side": "buy",
            "qty": 100,
            "order_type": "plage",
            "limit_price": 42.9,
            "trigger_price": 42.4,
        },
    )
    assert r.status_code == 200 and r.json()["position_created"] is False
    assert client.get("/api/positions").json()["positions"] == []
    assert len(client.get("/api/positions").json()["open_orders"]) == 1
    body = {
        "account": "cto_srd",
        "isin": "FR0000120271",
        "side": "buy",
        "qty": 100,
        "price": 42.4,
        "ts": datetime(2026, 9, 15, 7, 30, tzinfo=UTC).isoformat(),
        "order_id": r.json()["order_id"],
    }
    e1 = client.post("/api/executions", json=body).json()
    e2 = client.post("/api/executions", json=body).json()
    assert e1["created"] and not e2["created"] and e1["trade_id"] == e2["trade_id"]
    assert e1["execution_state"] == "filled" and e1["protection_state"] == "unprotected"
    pos = client.get("/api/positions").json()["positions"][0]
    assert pos["qty_unprotected"] == 100 and pos["state_fresh"] is False and pos["state_declarative"] is True
    assert client.get("/api/positions").json()["open_orders"] == []  # filled
    p = client.post(
        "/api/protections",
        json={"position_id": e1["position_id"], "level": 40.6, "order_type": "seuil", "qty_covered": 100},
    ).json()
    assert p["protection_state"] == "protected"
    assert (
        client.post(
            "/api/protections",
            json={
                "position_id": e1["position_id"],
                "level": 39.0,
                "order_type": "seuil",
                "qty_covered": 100,
                "kind": "trailing",
            },
        ).status_code
        == 400
    )
    html = client.get("/positions").text
    assert "FR0000120271" in html and "protected" in html and "déclaratif" in html


def test_positions_page_forms(client):
    r = client.post(
        "/positions/execute",
        data={"account": "pea", "isin": "fr0000120073", "side": "buy", "qty": 12, "price": 171.45},
        follow_redirects=False,
    )
    assert r.status_code == 303
    html = client.get("/positions").text
    assert "NON PROTÉGÉS" in html and "FR0000120073" in html


def test_import_boursobank_operations(client):
    text = (FX / "boursobank_operations_anonymised.csv").read_bytes()
    r = client.post(
        "/import/boursobank",
        data={"kind": "operations"},
        files={"file": ("ops.csv", text, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "3 opérations lues" in unquote(r.headers["location"])
    positions = client.get("/api/positions").json()["positions"]
    isins = {p["isin"]: p for p in positions}
    assert isins["FR0000120271"]["qty_held"] == 40 and isins["FR0000120073"]["qty_held"] == 12
    r2 = client.post(
        "/import/boursobank",
        data={"kind": "operations"},
        files={"file": ("ops.csv", text, "text/csv")},
        follow_redirects=False,
    )
    assert "2 doublons ignorés" in unquote(r2.headers["location"])
    bad = client.post(
        "/import/boursobank",
        data={"kind": "positions"},
        files={"file": ("x.csv", b"a;b\n1;2\n", "text/csv")},
        follow_redirects=False,
    )
    assert "Format non reconnu" in unquote(bad.headers["location"])


def test_telegram_declaration_commands(client):
    from app.notify.telegram import ModeStore, handle_text

    events = []
    store = ModeStore(set_mode=lambda *a: None, current_mode=lambda: "reunion", log_event=lambda *a: events.append(a))
    r = handle_text("/exec cto_cash FR0000120271 10 42,40", 1, 1, store)
    assert "exécution déclarée" in r.text and "NON PROTÉGÉE" in r.text
    pid = client.get("/api/positions").json()["positions"][0]["position_id"]
    r2 = handle_text(f"/stop {pid} 40.6 10", 1, 1, store)
    assert "protected" in r2.text
    assert "Usage" in handle_text("/exec nimporte", 1, 1, store).text
    assert "refusé" in handle_text(f"/stop {pid} 30 10", 1, 1, store).text
