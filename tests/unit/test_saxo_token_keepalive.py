"""ADR-007: the Saxo refresh token rotates and expires within the hour — stored with its expiry,
kept alive by a job, and an expired authorisation is reported once a day, never as a job error."""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from app.data.providers.saxo import LIVE_AUTH, LIVE_BASE, SaxoAuthExpired, SaxoClient, TokenStore
from app.scheduler.jobs import saxo_token_keepalive
from app.scheduler.jobs.portfolio_sync import run as portfolio_sync_run
from app.scheduler.runner import JobContext

KEY = Fernet.generate_key().decode()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SAXO_TOKEN_KEY", KEY)
    monkeypatch.setenv("SAXO_APP_KEY", "k")
    monkeypatch.setenv("SAXO_APP_SECRET", "s")
    monkeypatch.setenv("SAXO_TOKEN_PATH", str(tmp_path / "saxo_token.enc"))
    monkeypatch.delenv("SAXO_ACCESS_TOKEN", raising=False)
    return TokenStore(live=True)


def test_token_store_keeps_expiry_and_reads_legacy_files(store):
    store.save("rt-1", refresh_expires_in=3600)
    assert store.load() == "rt-1"
    info = store.info()
    assert info and not info["expired"] and info["refresh_expires_at"] > time.time() + 3000
    # legacy file: bare token written by an earlier saxo-auth
    with open(store.path, "wb") as fh:
        fh.write(Fernet(KEY.encode()).encrypt(b"rt-legacy"))
    assert store.load() == "rt-legacy" and store.info() == {
        "saved_at": None,
        "refresh_expires_at": None,
        "expired": False,
    }
    assert not any(n.startswith(".saxo_token.") for n in __import__("os").listdir(store.path.rsplit("/", 1)[0]))


@respx.mock
def test_refresh_rotates_token_and_records_expiry(store):
    store.save("rt-1")
    respx.post(f"{LIVE_AUTH}/token").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "at", "expires_in": 1200, "refresh_token": "rt-2", "refresh_token_expires_in": 2400},
        )
    )
    c = SaxoClient(client=httpx.Client(), live=True)
    info = c.keepalive()
    assert store.load() == "rt-2" and 2300 < info["refresh_expires_at"] - time.time() <= 2400
    body = respx.calls.last.request.content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=rt-1" in body


@respx.mock
def test_expired_refresh_raises_saxo_auth_expired(store):
    store.save("rt-old")
    respx.post(f"{LIVE_AUTH}/token").mock(return_value=httpx.Response(401, json={"error": "invalid_grant"}))
    with pytest.raises(SaxoAuthExpired, match="saxo-auth"):
        SaxoClient(client=httpx.Client(), live=True).keepalive()


def _ctx(config, calendars):
    from datetime import date

    from app.notify.base import Notifier
    from app.services.alerts import AlertService

    sent: list = []
    c = JobContext(config, calendars, date(2026, 10, 20), ("XPAR",), "normal")
    c.alerts = AlertService(Notifier(telegram_send=lambda e: sent.append(e)), 15)
    c.mode = "reunion"
    c.sent = sent  # type: ignore[attr-defined]
    return c


@respx.mock
def test_keepalive_job_reports_expired_auth_once_a_day(store, db_engine, config, calendars):
    store.save("rt-old")
    respx.post(f"{LIVE_AUTH}/token").mock(return_value=httpx.Response(401, json={"error": "invalid_grant"}))
    ctx = _ctx(config, calendars)
    assert saxo_token_keepalive.run(ctx) == 0  # never a job error
    assert saxo_token_keepalive.run(ctx) == 0
    kinds = [e.kind for e in ctx.sent]
    assert kinds == ["saxo_auth_expired"] and "saxo-auth" in ctx.sent[0].telegram_text()  # deduplicated per day


@respx.mock
def test_portfolio_sync_handles_expired_auth_without_error(store, db_engine, config, calendars):
    store.save("rt-old")
    respx.post(f"{LIVE_AUTH}/token").mock(return_value=httpx.Response(401, json={"error": "invalid_grant"}))
    respx.get(f"{LIVE_BASE}/port/v1/positions").mock(return_value=httpx.Response(200, json={"Data": []}))
    ctx = _ctx(config, calendars)
    ctx.variant = "intraday"
    assert portfolio_sync_run(ctx) == 0
    assert [e.kind for e in ctx.sent] == ["saxo_auth_expired"]


def test_keepalive_job_skips_when_not_configured(monkeypatch, config, calendars):
    monkeypatch.delenv("SAXO_TOKEN_KEY", raising=False)
    assert saxo_token_keepalive.run(_ctx(config, calendars)) == 0


def test_keepalive_is_scheduled_every_day(config):
    from app.scheduler.registry import build_schedule

    jobs = {j.name: j for j in build_schedule(config.params)}
    assert "saxo_token_keepalive" in jobs and not jobs["saxo_token_keepalive"].spec.market_days_only
    assert "sun" in str(jobs["saxo_token_keepalive"].trigger) and "*/20" in str(jobs["saxo_token_keepalive"].trigger)
