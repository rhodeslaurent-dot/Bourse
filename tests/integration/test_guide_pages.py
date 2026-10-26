"""User requests of 26/10/2026: a flow diagram of the tool with the real last run of each feature, a help page
(reading the pages, glossary, setup, reconnecting the links), and an opportunities page that shows what can be
acted on first — the n°1 of the raw ranking may stay « à surveiller » and out of the order book."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import JobRun
from app.db.session import db_session
from app.main import create_app
from app.services import engine
from tests.integration.test_engine import DAY, NOW, _seed, breakout_bars


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def test_flow_page_shows_the_last_run_of_each_feature(client):
    now = datetime.now(UTC)
    with db_session() as s:
        s.add(JobRun(job="opportunity_scan", started_at=now - timedelta(hours=1), ended_at=now - timedelta(hours=1),
                     status="ok", note="812 valeurs, 9 configurations"))  # fmt: skip
        s.add(JobRun(job="imap_poll", started_at=now, ended_at=now, status="error", error="IMAP_PASSWORD absent"))
    page = client.get("/fonctionnement").text
    assert "Fonctionnement de l'outil" in page and "Moteur de propositions" in page
    assert "812 valeurs, 9 configurations" in page and "IMAP_PASSWORD absent" in page
    assert "jamais exécuté" in page  # a job that never ran says so, never a guessed time


def test_help_page_explains_the_lists_and_the_admission_ratio(client):
    page = client.get("/aide").text
    assert "Watchlist, opportunités, carnet d'ordres" in page and "ratio d&#39;admission" in page.replace("'", "&#39;")
    assert "saxo-auth" in page and "media-check" in page


def test_opportunities_group_the_actionable_first_with_plain_reasons(client, config):
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
    page = client.get("/opportunites").text  # created today (wall clock)
    assert "Par place" in page and "comment lire cette page" in page
    assert "Actionnables aujourd" in page or "À surveiller — pas d" in page
    if "WATCH :" in page:
        assert "En clair :" in page
