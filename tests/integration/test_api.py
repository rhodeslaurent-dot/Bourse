"""TestClient integration: /health, /sante, /api/mode, access middleware, jobs_runs."""

import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.scheduler.runner import execute_job


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    for var in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "TELEGRAM_BOT_TOKEN", "HEALTHCHECKS_PING_KEY"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def test_health_and_api_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    body = client.get("/api/health").json()
    assert body["version"] == "2026.09.3" and body["disabled_features"] == []


def test_sante_page_renders(client):
    r = client.get("/sante")
    assert r.status_code == 200
    assert "Santé du système" in r.text
    assert "DÉSACTIVÉ" in r.text  # Cloudflare Access not configured in tests → shown, never hidden
    assert "non confirmé" in r.text  # unconfirmed market_open slot is visible


def test_mode_api_and_override(client):
    assert client.get("/api/mode").json()["mode"] in ("disponible", "reunion", "absent")
    r = client.post("/api/mode", json={"mode": "absent", "duration_minutes": 120})
    assert r.status_code == 200 and r.json()["mode"] == "absent" and r.json()["until"]
    assert client.get("/api/mode").json()["source"] == "web"
    assert client.post("/api/mode", json={"mode": "vacances"}).status_code == 422
    r = client.post("/mode", data={"mode": "disponible"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/mode").json()["mode"] == "disponible"


def test_access_middleware_requires_token_when_configured(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "example.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "aud123")
    monkeypatch.setenv("APP_API_TOKEN", "s3cret")
    app = create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # exempt
        assert c.get("/sante").status_code == 401
        assert c.get("/api/mode", headers={"Cf-Access-Jwt-Assertion": "garbage"}).status_code == 401
        assert c.get("/api/mode", headers={"X-App-Token": "s3cret"}).status_code == 200
        assert c.get("/api/mode", headers={"X-App-Token": "wrong"}).status_code == 401


def test_test_job_writes_jobs_runs_and_respects_calendar(client, config, calendars):
    run = execute_job("test_job", config, calendars, run_date=date(2026, 9, 14))
    assert run.status == "ok" and run.rows == 4
    skipped = execute_job("test_job", config, calendars, run_date=date(2026, 12, 25))
    assert skipped.status == "skipped" and "aucune place ouverte" in skipped.note
    jobs = client.get("/api/jobs").json()
    assert jobs["last_runs"]["test_job"]["status"] == "skipped"
    assert "test_job" in client.get("/sante").text


def test_job_error_is_recorded_not_raised(client, config, calendars):
    def boom(ctx):
        raise RuntimeError("provider down")

    run = execute_job("test_job", config, calendars, run_date=date(2026, 9, 14), fn=boom)
    assert run.status == "error" and "provider down" in run.error


def test_watchdog_ping_called(config, calendars, db_engine, monkeypatch):
    calls = []
    monkeypatch.setenv("HEALTHCHECKS_PING_KEY", "abc")
    import app.scheduler.runner as runner

    monkeypatch.setattr(runner.httpx, "get", lambda url, timeout=10: calls.append(url))
    execute_job("test_job", config, calendars, run_date=date(2026, 9, 14))
    assert calls == ["https://hc-ping.com/abc/test_job?create=1"]
    assert os.environ.get("HEALTHCHECKS_PING_KEY") == "abc"


def test_writes_refused_without_access_unless_allowed(config, calendars, db_engine, monkeypatch):
    for var in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "ALLOW_UNAUTHENTICATED_WRITES", "APP_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        assert c.get("/sante").status_code == 200  # read-only pages still visible (they show the red warning)
        assert c.post("/api/mode", json={"mode": "absent"}).status_code == 503
        assert (
            c.post(
                "/api/executions", json={"account": "pea", "isin": "FR0000120271", "side": "buy", "qty": 1, "price": 1}
            ).status_code
            == 503
        )
        monkeypatch.setenv("APP_API_TOKEN", "tok")
        assert c.post("/api/mode", json={"mode": "absent"}, headers={"X-App-Token": "tok"}).status_code == 200
